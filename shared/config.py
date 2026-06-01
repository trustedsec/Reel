"""
Shared configuration management - Hybrid .env + database approach
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    """Base configuration class - Hybrid approach using .env fallback + database"""
    
    # Critical settings that always come from .env (for security/bootstrapping)
    # These must be set in environment variables - no defaults allowed
    _SECRET_KEY = os.getenv('SECRET_KEY')
    _JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY')
    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///reel.db')
    
    @classmethod
    def _validate_secrets(cls):
        """Validate that secret keys are set and not using defaults"""
        if not cls._SECRET_KEY or cls._SECRET_KEY in ('dev-key-change-this', ''):
            raise ValueError(
                "SECRET_KEY must be set in environment variables. "
                "Please set SECRET_KEY in your .env file or environment."
            )
        if not cls._JWT_SECRET_KEY or cls._JWT_SECRET_KEY in ('jwt-secret-change-this', ''):
            raise ValueError(
                "JWT_SECRET_KEY must be set in environment variables. "
                "Please set JWT_SECRET_KEY in your .env file or environment."
            )
    
    @property
    def SECRET_KEY(self):
        """Get SECRET_KEY with validation"""
        self._validate_secrets()
        return self._SECRET_KEY
    
    @property
    def JWT_SECRET_KEY(self):
        """Get JWT_SECRET_KEY with validation"""
        self._validate_secrets()
        return self._JWT_SECRET_KEY
    
    # Settings that can be managed via admin UI (with .env fallback)
    _db_manageable_settings = {
        # Server configuration
        'ADMIN_HOST': {'default': '127.0.0.1', 'type': 'string', 'category': 'server'},
        'ADMIN_PORT': {'default': 8000, 'type': 'int', 'category': 'server'},
        'PHISHING_HOST': {'default': '0.0.0.0', 'type': 'string', 'category': 'server'},
        'PHISHING_PORT': {'default': 1234, 'type': 'int', 'category': 'server'},
        
        # File upload settings
        'MAX_FILE_SIZE': {'default': 10 * 1024 * 1024, 'type': 'int', 'category': 'files'},
        'UPLOAD_FOLDER': {'default': 'storage/uploads', 'type': 'string', 'category': 'files'},
        'TEMPLATES_FOLDER': {'default': 'storage/templates', 'type': 'string', 'category': 'files'},
        'ASSETS_FOLDER': {'default': 'storage/assets', 'type': 'string', 'category': 'files'},
        
        # External services
        'GRAPHSPY_URL': {'default': 'http://localhost:5000', 'type': 'string', 'category': 'external', 'description': 'GraphSpy API endpoint for Azure device codes'},
        'PUSHOVER_API_TOKEN': {'default': '', 'type': 'string', 'category': 'external', 'is_sensitive': True, 'description': 'Pushover API token for notifications'},
        'PUSHOVER_USER_KEY': {'default': '', 'type': 'string', 'category': 'external', 'is_sensitive': True, 'description': 'Pushover user key for notifications'},
        
        # Security settings
        'SESSION_TIMEOUT': {'default': 3600, 'type': 'int', 'category': 'security', 'description': 'Session timeout in seconds'},
        'MAX_LOGIN_ATTEMPTS': {'default': 5, 'type': 'int', 'category': 'security', 'description': 'Maximum failed login attempts before lockout'},
        'LOCKOUT_DURATION': {'default': 900, 'type': 'int', 'category': 'security', 'description': 'Account lockout duration in seconds'},
        
        # Caddy configuration  
        'CADDY_API_URL': {'default': 'http://localhost:2019', 'type': 'string', 'category': 'caddy', 'description': 'Caddy API endpoint'},
        'CADDY_STORAGE_BASE': {'default': 'storage', 'type': 'string', 'category': 'caddy'},
        'CADDY_EMAIL': {'default': '', 'type': 'string', 'category': 'caddy', 'description': 'Email for Let\'s Encrypt certificates'},
        'CADDY_DEFAULT_DOMAIN': {'default': 'localhost', 'type': 'string', 'category': 'caddy'},
        'CADDY_ENABLE_STAGING': {'default': False, 'type': 'bool', 'category': 'caddy', 'description': 'Use Let\'s Encrypt staging environment'},
        'CADDY_LOG_LEVEL': {'default': 'INFO', 'type': 'string', 'category': 'caddy'},
    }
    
    @classmethod
    def get_setting(cls, key, default=None):
        """Get setting value - checks database first, then .env, then default"""
        try:
            # Import here to avoid circular imports during app startup
            from shared.database import Setting
            
            # Try database first
            db_value = Setting.get_setting(key)
            if db_value is not None:
                return db_value
            
        except (ImportError, Exception):
            # Database not available yet (during startup) - fall back to .env
            pass
        
        # Fall back to .env then default
        env_value = os.getenv(key, default)
        
        # Type conversion based on setting definition
        if key in cls._db_manageable_settings:
            setting_def = cls._db_manageable_settings[key]
            if setting_def['type'] == 'int' and isinstance(env_value, str):
                try:
                    return int(env_value)
                except ValueError:
                    return setting_def['default']
            elif setting_def['type'] == 'bool' and isinstance(env_value, str):
                return env_value.lower() in ('true', '1', 'yes', 'on')
        
        return env_value
    
    # Dynamic properties that check database first, then .env
    @property
    def ADMIN_HOST(self):
        return self.get_setting('ADMIN_HOST', '127.0.0.1')
    
    @property 
    def ADMIN_PORT(self):
        return self.get_setting('ADMIN_PORT', 8000)
    
    @property
    def PHISHING_HOST(self):
        return self.get_setting('PHISHING_HOST', '0.0.0.0')
    
    @property
    def PHISHING_PORT(self):
        return self.get_setting('PHISHING_PORT', 1234)
    
    @property
    def MAX_CONTENT_LENGTH(self):
        return self.get_setting('MAX_FILE_SIZE', 10 * 1024 * 1024)
    
    @property
    def UPLOAD_FOLDER(self):
        return Path(self.get_setting('UPLOAD_FOLDER', 'storage/uploads'))
    
    @property
    def TEMPLATES_FOLDER(self):
        return Path(self.get_setting('TEMPLATES_FOLDER', 'storage/templates'))
    
    @property
    def ASSETS_FOLDER(self):
        return Path(self.get_setting('ASSETS_FOLDER', 'storage/assets'))
    
    @property
    def GRAPHSPY_URL(self):
        return self.get_setting('GRAPHSPY_URL', 'http://localhost:5000')
    
    @property
    def PUSHOVER_API_TOKEN(self):
        return self.get_setting('PUSHOVER_API_TOKEN', '')
    
    @property
    def PUSHOVER_USER_KEY(self):
        return self.get_setting('PUSHOVER_USER_KEY', '')
    
    @property
    def SESSION_TIMEOUT(self):
        return self.get_setting('SESSION_TIMEOUT', 3600)
    
    @property
    def MAX_LOGIN_ATTEMPTS(self):
        return self.get_setting('MAX_LOGIN_ATTEMPTS', 5)
    
    @property
    def LOCKOUT_DURATION(self):
        return self.get_setting('LOCKOUT_DURATION', 900)
    
    @property
    def CADDY_API_URL(self):
        return self.get_setting('CADDY_API_URL', 'http://localhost:2019')
    
    @property
    def CADDY_STORAGE_BASE(self):
        return Path(self.get_setting('CADDY_STORAGE_BASE', 'storage'))
    
    @property
    def CADDY_EMAIL(self):
        return self.get_setting('CADDY_EMAIL', '')
    
    @property
    def CADDY_DEFAULT_DOMAIN(self):
        return self.get_setting('CADDY_DEFAULT_DOMAIN', 'localhost')
    
    @property
    def CADDY_ENABLE_STAGING(self):
        return self.get_setting('CADDY_ENABLE_STAGING', False)
    
    @property
    def CADDY_LOG_LEVEL(self):
        return self.get_setting('CADDY_LOG_LEVEL', 'INFO')
    
    @classmethod
    def init_app(cls, app):
        """Initialize application with configuration"""
        # Validate secrets before proceeding
        cls._validate_secrets()
        
        # Create necessary directories
        config_instance = cls()
        config_instance.UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
        config_instance.TEMPLATES_FOLDER.mkdir(parents=True, exist_ok=True)
        config_instance.ASSETS_FOLDER.mkdir(parents=True, exist_ok=True)
        
        # Create plugins directory
        Path('storage/plugins').mkdir(parents=True, exist_ok=True)
        
        # Create logs directory
        Path('logs').mkdir(exist_ok=True)
        
        # Set secret keys in Flask config (validated above)
        app.config['SECRET_KEY'] = config_instance.SECRET_KEY
        app.config['JWT_SECRET_KEY'] = config_instance.JWT_SECRET_KEY
        
        # Override Flask config with actual property values (not property objects)
        app.config['MAX_CONTENT_LENGTH'] = config_instance.MAX_CONTENT_LENGTH
        app.config['UPLOAD_FOLDER'] = str(config_instance.UPLOAD_FOLDER)
        app.config['TEMPLATES_FOLDER'] = str(config_instance.TEMPLATES_FOLDER)
        app.config['ASSETS_FOLDER'] = str(config_instance.ASSETS_FOLDER)
        
        # Override other dynamic properties Flask might need
        app.config['ADMIN_HOST'] = config_instance.ADMIN_HOST
        app.config['ADMIN_PORT'] = config_instance.ADMIN_PORT
        app.config['PHISHING_HOST'] = config_instance.PHISHING_HOST
        app.config['PHISHING_PORT'] = config_instance.PHISHING_PORT
        app.config['SESSION_TIMEOUT'] = config_instance.SESSION_TIMEOUT
        app.config['MAX_LOGIN_ATTEMPTS'] = config_instance.MAX_LOGIN_ATTEMPTS
        app.config['LOCKOUT_DURATION'] = config_instance.LOCKOUT_DURATION

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    
class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    
class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    DATABASE_URL = 'sqlite:///:memory:'
