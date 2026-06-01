"""
Global error handlers and custom exceptions
"""
from flask import jsonify, request, render_template, g
from werkzeug.exceptions import HTTPException
from pydantic import ValidationError
import logging

logger = logging.getLogger(__name__)

class ReelException(Exception):
    """Base exception class for Reel application"""
    def __init__(self, message, status_code=500, payload=None):
        super().__init__()
        self.message = message
        self.status_code = status_code
        self.payload = payload

class ValidationException(ReelException):
    """Exception for validation errors"""
    def __init__(self, message, errors=None):
        super().__init__(message, status_code=400)
        self.errors = errors or []

class AuthenticationException(ReelException):
    """Exception for authentication errors"""
    def __init__(self, message="Authentication required"):
        super().__init__(message, status_code=401)

class AuthorizationException(ReelException):
    """Exception for authorization errors"""
    def __init__(self, message="Insufficient privileges"):
        super().__init__(message, status_code=403)

class CampaignNotFoundException(ReelException):
    """Exception for when a campaign is not found"""
    def __init__(self, uid):
        super().__init__(f"Campaign with UID {uid} not found", status_code=404)

class TemplateNotFoundException(ReelException):
    """Exception for when a template is not found"""
    def __init__(self, template_id):
        super().__init__(f"Template with ID {template_id} not found", status_code=404)

def is_api_request():
    """Check if the request is for API endpoint"""
    return request.path.startswith('/api/') or request.headers.get('Content-Type') == 'application/json'

def register_error_handlers(app):
    """Register global error handlers"""
    
    @app.errorhandler(ReelException)
    def handle_reel_exception(error):
        """Handle custom Reel exceptions"""
        logger.error(f"ReelException: {error.message}")
        
        response = {
            'error': error.__class__.__name__,
            'message': error.message
        }
        
        if hasattr(error, 'errors') and error.errors:
            response['errors'] = error.errors
        
        if error.payload:
            response.update(error.payload)
        
        if is_api_request():
            return jsonify(response), error.status_code
        
        # For web requests, render error template
        return render_template('errors/error.html', 
                             error=error, 
                             title=f"Error {error.status_code}"), error.status_code
    
    @app.errorhandler(ValidationError)
    def handle_validation_error(error):
        """Handle Pydantic validation errors"""
        logger.error(f"Validation error: {error}")
        
        # Check if we're in debug mode
        is_debug = app.config.get('DEBUG', False)
        
        errors = []
        for err in error.errors():
            if is_debug:
                errors.append({
                    'field': '.'.join(str(x) for x in err['loc']),
                    'message': err['msg'],
                    'type': err['type']
                })
            else:
                # Generic error message in production
                errors.append({
                    'field': '.'.join(str(x) for x in err['loc']),
                    'message': 'Invalid value'
                })
        
        response = {
            'error': 'ValidationError',
            'message': 'Input validation failed' if is_debug else 'Invalid input provided',
            'errors': errors
        }
        
        if is_api_request():
            return jsonify(response), 400
        
        return render_template('errors/validation.html', 
                             errors=errors,
                             title="Validation Error"), 400
    
    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        """Handle HTTP exceptions"""
        logger.error(f"HTTP Exception: {error.code} - {error.description}")
        
        # Check if we're in debug mode
        is_debug = app.config.get('DEBUG', False)
        
        response = {
            'error': error.name,
            'message': error.description if is_debug else 'An error occurred',
            'status_code': error.code
        }
        
        if is_api_request():
            return jsonify(response), error.code
        
        # For phishing endpoints, we want to show generic error pages
        if request.path.startswith('/api/') or request.path.startswith('/admin/'):
            template = 'errors/error.html'
        else:
            # For phishing endpoints, show a generic 404 to avoid revealing campaign structure
            template = 'errors/phishing_error.html'
        
        return render_template(template, 
                             error=error,
                             title=f"Error {error.code}"), error.code
    
    @app.errorhandler(Exception)
    def handle_generic_exception(error):
        """Handle unexpected exceptions"""
        logger.exception(f"Unexpected error: {error}")
        
        # Check if we're in debug mode
        is_debug = app.config.get('DEBUG', False)
        
        response = {
            'error': 'InternalServerError',
            'message': 'An unexpected error occurred' if is_debug else 'An error occurred. Please contact support.'
        }
        
        # Never expose debug info in production
        if is_debug:
            response['debug_info'] = str(error)
        
        if is_api_request():
            return jsonify(response), 500
        
        return render_template('errors/error.html',
                             error_message="An unexpected error occurred" if is_debug else "An error occurred. Please contact support.",
                             title="Server Error"), 500
    
    @app.errorhandler(404)
    def handle_not_found(error):
        """Handle 404 errors with special handling for phishing endpoints"""
        
        if is_api_request():
            return jsonify({
                'error': 'NotFound',
                'message': 'The requested resource was not found'
            }), 404
        
        # For phishing endpoints: check per-campaign custom 404 (routing 404s hit app handler, not blueprint)
        if not (request.path.startswith('/api/') or request.path.startswith('/admin/')):
            campaign_obj = g.get('campaign_obj')
            # Blueprint before_request doesn't run when no route matched, so load campaign here
            if not campaign_obj:
                from shared.database import Campaign
                uid = request.headers.get('X-Campaign-ID')
                if not uid:
                    path_parts = request.path.strip('/').split('/')
                    if path_parts and path_parts[0]:
                        uid = path_parts[0]
                if uid and uid not in ('static', 'assets', 'favicon.ico', 'robots.txt', 'health'):
                    campaign_obj = Campaign.query.filter_by(
                        uid=uid, status='active', campaign_type='inbound'
                    ).first()
            if campaign_obj and campaign_obj.campaign_type == 'inbound':
                config = campaign_obj.config or {}
                custom_body = config.get('custom_404_body', '').strip()
                template_id = config.get('custom_404_template_id')

                if custom_body:
                    return custom_body, 404

                if template_id:
                    from shared.database import Template
                    from shared.template_render import render_sandboxed
                    template = Template.query.get(template_id)
                    if template and template.template_html:
                        try:
                            template_context = {
                                'campaign': {
                                    'name': campaign_obj.name,
                                    'uid': campaign_obj.uid
                                },
                                'request': {
                                    'ip_address': request.remote_addr,
                                    'user_agent': request.headers.get('User-Agent'),
                                    'path': request.path
                                },
                                'variables': campaign_obj.variables or {}
                            }
                            rendered = render_sandboxed(template.template_html, template_context)
                            return rendered, 404
                        except Exception as e:
                            logger.warning(f"Failed to render custom 404 template: {e}")

            return render_template('errors/phishing_404.html'), 404
        
        return render_template('errors/404.html'), 404
    
    @app.errorhandler(500)
    def handle_internal_server_error(error):
        """Handle 500 errors"""
        logger.exception("Internal server error")
        
        if is_api_request():
            return jsonify({
                'error': 'InternalServerError',
                'message': 'Internal server error'
            }), 500
        
        # For phishing endpoints, show generic error to avoid information disclosure
        if not (request.path.startswith('/api/') or request.path.startswith('/admin/')):
            return render_template('errors/phishing_500.html'), 500
        
        return render_template('errors/500.html'), 500

def log_security_event(event_type, details, request_info=None):
    """Log security-related events"""
    if request_info is None:
        request_info = {
            'ip': request.remote_addr,
            'user_agent': request.headers.get('User-Agent'),
            'path': request.path,
            'method': request.method
        }
    
    logger.warning(f"SECURITY EVENT - {event_type}: {details}", extra={
        'security_event': True,
        'event_type': event_type,
        'details': details,
        'request_info': request_info
    })
