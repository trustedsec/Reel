"""
Email validator plugin - validates email templates
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
import logging
import re
from jinja2 import Environment, TemplateSyntaxError

logger = logging.getLogger(__name__)

class EmailValidatorPlugin(BasePlugin):
    """
    Plugin for validating email templates for syntax, content quality, and spam indicators.
    
    Validates email templates (HTML and/or plain text) for Jinja2 syntax errors,
    content quality issues, and spam indicators. Can use AI for advanced validation.
    Stores validation results in context for conditional branching.
    
    Use cases:
    - Validate email templates before sending
    - Check for syntax errors in templates
    - Detect spam indicators that might trigger filters
    - Ensure email quality before campaigns
    - AI-powered content analysis
    
    Example: Validate email template, branch workflow based on validation result
    (valid/invalid), and only send if validation passes.
    """
    
    @property
    def plugin_type(self) -> str:
        return "email_validator"
    
    @property
    def display_name(self) -> str:
        return "Email Template Validator"
    
    @property
    def description(self) -> str:
        return "Validate email templates for Jinja2 syntax, content quality, and spam indicators. Supports AI-powered validation. Use to ensure email quality before sending, detect issues, and branch workflows based on validation results."
    
    @property
    def plugin_category(self) -> str:
        return "email_validation"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "template_html": {
                    "type": "string",
                    "title": "HTML Template",
                    "description": "Email HTML template to validate",
                    "help": "HTML email template content to validate. Can include Jinja2 template syntax ({{variables}}, {% blocks %}). Supports variable interpolation: {{template_html}} or direct input.",
                    "placeholder": "<html><body><h1>{{target.name}}</h1></body></html>"
                },
                "template_text": {
                    "type": "string",
                    "title": "Plain Text Template",
                    "description": "Email plain text template to validate",
                    "help": "Plain text email template content to validate. Can include Jinja2 template syntax. Used as fallback for email clients that don't support HTML. Supports variable interpolation.",
                    "placeholder": "Hello {{target.name}}, your email is {{target.email}}"
                },
                "validate_syntax": {
                    "type": "boolean",
                    "title": "Validate Syntax",
                    "description": "Check Jinja2 template syntax for errors",
                    "help": "When enabled, validates that Jinja2 template syntax is correct. Checks for unclosed tags, invalid syntax, and template errors. Recommended: Keep enabled to catch syntax errors early.",
                    "default": True
                },
                "validate_content": {
                    "type": "boolean",
                    "title": "Validate Content Quality",
                    "description": "Check email content for quality issues",
                    "help": "When enabled, validates email content quality. Checks for missing HTML structure (<body> tag), very short templates, unclosed HTML tags, and lack of dynamic content. Recommended: Keep enabled.",
                    "default": True
                },
                "check_spam": {
                    "type": "boolean",
                    "title": "Check Spam Indicators",
                    "description": "Check for spam indicator words and patterns",
                    "help": "When enabled, checks for common spam indicator words (free, click here, urgent, etc.), excessive links, and excessive capitalization. Helps identify content that might trigger spam filters. Optional but recommended for production emails.",
                    "default": False
                },
                "ai_validation": {
                    "type": "boolean",
                    "title": "AI Validation",
                    "description": "Use AI for advanced content validation",
                    "help": "When enabled, uses AI service for advanced content analysis and suggestions. Currently a placeholder for future AI integration. Will provide intelligent suggestions for improving email content.",
                    "default": False
                }
            }
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute email validation"""
        try:
            validation_results = {
                'valid': True,
                'errors': [],
                'warnings': [],
                'suggestions': []
            }
            
            template_html = config.get('template_html', '')
            template_text = config.get('template_text', '')
            
            # Validate syntax
            if config.get('validate_syntax', True):
                syntax_errors = self._validate_syntax(template_html, template_text)
                validation_results['errors'].extend(syntax_errors)
            
            # Validate content
            if config.get('validate_content', True):
                content_issues = self._validate_content(template_html, template_text)
                validation_results['warnings'].extend(content_issues)
            
            # Check spam indicators
            if config.get('check_spam', False):
                spam_issues = self._check_spam_indicators(template_html, template_text)
                validation_results['warnings'].extend(spam_issues)
            
            # AI validation (placeholder for future AI integration)
            if config.get('ai_validation', False):
                ai_results = self._ai_validate(template_html, template_text)
                validation_results['suggestions'].extend(ai_results.get('suggestions', []))
                validation_results['warnings'].extend(ai_results.get('warnings', []))
            
            validation_results['valid'] = len(validation_results['errors']) == 0
            
            context['email_validation'] = validation_results
            
            logger.info(f"Email validation completed: {'valid' if validation_results['valid'] else 'invalid'}")
            
        except Exception as e:
            logger.error(f"Email validation failed: {e}")
            return self.on_error(e, context, config)
        
        return context
    
    def get_branch_context_key(self) -> Optional[str]:
        """Return the context key that contains the email validation result"""
        return "email_validation.valid"
    
    def get_branch_labels(self) -> Dict[str, str]:
        """Return custom labels for valid/invalid paths"""
        return {"true": "Valid", "false": "Invalid"}
    
    def _validate_syntax(self, html: str, text: str) -> List[str]:
        """Validate Jinja2 template syntax"""
        errors = []
        env = Environment()
        
        try:
            if html:
                env.parse(html)
        except TemplateSyntaxError as e:
            errors.append(f"HTML template syntax error: {e}")
        
        try:
            if text:
                env.parse(text)
        except TemplateSyntaxError as e:
            errors.append(f"Text template syntax error: {e}")
        
        return errors
    
    def _validate_content(self, html: str, text: str) -> List[str]:
        """Validate email content quality"""
        warnings = []
        
        # Check for required elements
        if html and '<body' not in html.lower():
            warnings.append("HTML template missing <body> tag")
        
        if html and '{{' not in html and '{%' not in html:
            warnings.append("HTML template appears to have no dynamic content")
        
        # Check for common issues
        if html and len(html) < 100:
            warnings.append("HTML template is very short, may be incomplete")
        
        if text and len(text) < 50:
            warnings.append("Text template is very short, may be incomplete")
        
        # Check for unclosed tags
        if html:
            open_tags = re.findall(r'<([^/>]+)>', html)
            close_tags = re.findall(r'</([^>]+)>', html)
            # Simple check - not perfect but catches obvious issues
            if len(open_tags) > len(close_tags) * 1.5:
                warnings.append("Possible unclosed HTML tags detected")
        
        return warnings
    
    def _check_spam_indicators(self, html: str, text: str) -> List[str]:
        """Check for spam indicators"""
        warnings = []
        content = (html + ' ' + text).lower()
        
        spam_words = ['free', 'click here', 'limited time', 'act now', 'urgent', 'winner']
        spam_count = sum(1 for word in spam_words if word in content)
        
        if spam_count > 3:
            warnings.append(f"Multiple spam indicator words detected ({spam_count})")
        
        # Check for excessive links
        link_count = len(re.findall(r'<a\s+href', html))
        if link_count > 10:
            warnings.append(f"High number of links detected ({link_count}), may trigger spam filters")
        
        # Check for excessive capitalization
        if text:
            caps_ratio = sum(1 for c in text if c.isupper()) / len(text) if text else 0
            if caps_ratio > 0.3:
                warnings.append("High ratio of capital letters, may trigger spam filters")
        
        return warnings
    
    def _ai_validate(self, html: str, text: str) -> Dict[str, List[str]]:
        """AI-augmented validation (placeholder for future implementation)"""
        # This will be integrated with AI service in the future
        # For now, return empty results
        logger.debug("AI validation called (not yet implemented)")
        return {
            'suggestions': [],
            'warnings': []
        }
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate validator configuration"""
        errors = []
        
        if not config.get('template_html') and not config.get('template_text'):
            errors.append("At least one template (HTML or text) is required")
        
        return errors if errors else None

