# Main Flask application entry point
from flask import Flask
from datetime import timedelta
import os
from werkzeug.middleware.proxy_fix import ProxyFix

from shared.config import Config
from shared.database import init_app as init_database
from shared.auth import init_app as init_auth
from plugins import get_registry


def _warm_phishing_detector(app):
    """Pre-load the BERT phishing-detection model so the first template-create
    or template-update doesn't pay the multi-second download/init cost on the
    request thread. Failures (missing optional deps, network) are logged but
    must not break app startup."""
    try:
        from plugins import get_plugin
        plugin = get_plugin('phishing_detector')
        if plugin is None:
            app.logger.info("Phishing detector plugin not registered; skipping warmup")
            return
        plugin._load_model()
        app.logger.info("Phishing detector model warmed at startup")
    except Exception as e:
        app.logger.warning(
            "Phishing detector warmup failed (template scoring will still work "
            "on demand, but the first request will be slow): %s", e
        )


def create_app(config_name='development'):
    """Application factory pattern - Phishing server"""
    app = Flask(__name__, template_folder='phishing/templates')
    
    # Load configuration
    app.config.from_object(Config)
    Config.init_app(app)

    # Initialize extensions
    init_database(app)
    init_auth(app)

    # Session security and lifetime (for CAPTCHA gating and tracking; consistent with admin app)
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(seconds=app.config.get('SESSION_TIMEOUT', 3600))
    app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') == 'production'

    # Initialize plugin system
    with app.app_context():
        plugin_registry = get_registry()
        from api.services.plugin_service import PluginService
        plugin_service = PluginService()
        plugin_service.sync_builtin_plugins()

    # Start sync proxy manager for real-time credential relay
    with app.app_context():
        from workflows.sync_proxy_manager import start_sync_proxy_manager
        start_sync_proxy_manager()

    # Register only phishing blueprint
    from phishing.routes import phishing_bp
    app.register_blueprint(phishing_bp, url_prefix='/')
    
    # Global error handlers
    from shared.errors import register_error_handlers
    register_error_handlers(app)

    # Trust X-Forwarded-Proto when behind Caddy/reverse proxy (affects request.is_secure,
    # redirect URLs, and cookie handling; required for HTTPS behind localhost proxy)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)

    return app

def create_admin_app(skip_plugin_sync=False):
    """Create separate admin application"""
    app = Flask(__name__, 
                static_folder='admin/static',
                template_folder='admin/templates')
    
    # Load configuration
    app.config.from_object(Config)
    Config.init_app(app)
    
    # Initialize extensions
    init_database(app)
    init_auth(app)
    
    # Initialize plugin system
    with app.app_context():
        plugin_registry = get_registry()
        from api.services.plugin_service import PluginService
        plugin_service = PluginService()
        if not skip_plugin_sync:
            plugin_service.sync_builtin_plugins()
        # Warm the phishing-detection model in this process — TemplateService
        # calls it synchronously on template create/update, and the lazy
        # AutoModel.from_pretrained() on first request used to freeze the UI.
        _warm_phishing_detector(app)

    # Register API blueprint
    from api.routes import api_bp
    app.register_blueprint(api_bp, url_prefix='/api')
    
    # Register admin routes blueprint
    from admin.routes import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/')

    # Expose Python builtins that templates use (Jinja2 doesn't include hasattr by default)
    app.jinja_env.globals['hasattr'] = hasattr
    app.jinja_env.globals['getattr'] = getattr

    from shared.errors import register_error_handlers
    register_error_handlers(app)
    
    # Initialize CSRF protection
    from flask_wtf.csrf import CSRFProtect
    csrf = CSRFProtect()
    csrf.init_app(app)
    app.config['WTF_CSRF_ENABLED'] = True
    app.config['WTF_CSRF_TIME_LIMIT'] = None  # No time limit on CSRF tokens
    
    # Session security
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(seconds=app.config.get('SESSION_TIMEOUT', 3600))
    
    # Secure cookies in production only
    app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') == 'production'
    
    # Initialize Swagger for API documentation
    from flasgger import Swagger
    
    swagger_config = {
        "headers": [],
        "specs": [
            {
                "endpoint": "apispec",
                "route": "/apispec.json",
                "rule_filter": lambda rule: True,
                "model_filter": lambda tag: True,
            }
        ],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        "specs_route": "/api-docs"
    }
    
    swagger_template = {
        "swagger": "2.0",
        "info": {
            "title": "Reel v2 API",
            "description": "API documentation for Reel v2 campaign management system",
            "version": "2.0.0"
        },
        "basePath": "/api",
        "securityDefinitions": {
            "Bearer": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "JWT token. Format: 'Bearer {token}'"
            }
        },
        "security": [
            {
                "Bearer": []
            }
        ],
        "definitions": {
            "Campaign": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "campaign_type": {"type": "string", "enum": ["inbound", "outbound"]},
                    "uid": {"type": "string"},
                    "status": {"type": "string", "enum": ["draft", "active", "paused", "completed"]},
                    "config": {"type": "object"},
                    "variables": {"type": "object"},
                    "created_at": {"type": "string", "format": "date-time"},
                    "updated_at": {"type": "string", "format": "date-time"}
                }
            },
            "Template": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "template_type": {"type": "string"},
                    "template_html": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"},
                    "updated_at": {"type": "string", "format": "date-time"}
                }
            },
            "Workflow": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "workflow_type": {"type": "string", "enum": ["campaign", "sending"]},
                    "http_method": {"type": "string", "enum": ["GET", "POST", "BOTH"]},
                    "is_active": {"type": "boolean"},
                    "created_at": {"type": "string", "format": "date-time"}
                }
            },
            "Error": {
                "type": "object",
                "properties": {
                    "error": {"type": "string"},
                    "message": {"type": "string"},
                    "details": {"type": "object"}
                }
            },
            "Pagination": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "per_page": {"type": "integer"},
                    "total": {"type": "integer"},
                    "pages": {"type": "integer"},
                    "has_next": {"type": "boolean"},
                    "has_prev": {"type": "boolean"}
                }
            },
            "TrackedUser": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "campaign_id": {"type": "integer"},
                    "email": {"type": "string"},
                    "tracking_id": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"}
                }
            },
            "TrackingEvent": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "tracked_user_id": {"type": "integer"},
                    "event_type": {"type": "string"},
                    "ip_address": {"type": "string"},
                    "user_agent": {"type": "string"},
                    "additional_data": {"type": "object"},
                    "created_at": {"type": "string", "format": "date-time"}
                }
            },
            "Event": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "campaign_id": {"type": "integer"},
                    "event_type": {"type": "string"},
                    "data": {"type": "object"},
                    "ip_address": {"type": "string"},
                    "user_agent": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"}
                }
            },
            "Asset": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "template_id": {"type": "integer"},
                    "filename": {"type": "string"},
                    "file_path": {"type": "string"},
                    "file_size": {"type": "integer"},
                    "mime_type": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"}
                }
            },
            "Setting": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "description": {"type": "string"},
                    "category": {"type": "string"},
                    "updated_at": {"type": "string", "format": "date-time"}
                }
            },
            "Plugin": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "plugin_type": {"type": "string"},
                    "plugin_category": {"type": "string"},
                    "is_builtin": {"type": "boolean"},
                    "is_active": {"type": "boolean"},
                    "created_at": {"type": "string", "format": "date-time"}
                }
            },
            "CampaignStats": {
                "type": "object",
                "properties": {
                    "total_events": {"type": "integer"},
                    "total_tracked_users": {"type": "integer"},
                    "total_credentials_captured": {"type": "integer"},
                    "last_event_at": {"type": "string", "format": "date-time"}
                }
            },
            "SSLDetails": {
                "type": "object",
                "properties": {
                    "ssl_mode": {"type": "string"},
                    "custom_domain": {"type": "string"},
                    "ssl_cert_path": {"type": "string"},
                    "ssl_key_path": {"type": "string"},
                    "caddy_config_id": {"type": "string"}
                }
            }
        }
    }
    
    Swagger(app, config=swagger_config, template=swagger_template)
    
    # Protect Swagger UI routes - require authentication
    @app.before_request
    def protect_swagger_routes():
        """Protect Swagger UI and API spec routes"""
        from flask import request, redirect, url_for
        from flask_login import current_user
        
        # Protect Swagger routes
        if request.path.startswith('/api-docs') or request.path.startswith('/apispec'):
            if not current_user.is_authenticated:
                return redirect(url_for('admin.login', next=request.path))
    
    # Start background processors
    with app.app_context():
        from workflows.credential_proxy_processor import start_processor
        start_processor()
    
    return app

if __name__ == '__main__':
    # For direct execution, start admin server only
    admin_app = create_admin_app()
    admin_app.run(host='127.0.0.1', port=8000, debug=True)