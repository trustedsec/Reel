"""
Authentication and authorization utilities
"""
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, session, current_app, g
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from jose import JWTError, jwt
from passlib.context import CryptContext
from shared.database import db, User
import logging
import uuid

logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Flask-Login setup
login_manager = LoginManager()

def init_app(app):
    """Initialize authentication with Flask app"""
    login_manager.init_app(app)
    login_manager.login_view = 'admin.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    """Load user by ID for Flask-Login"""
    try:
        user = User.query.get(int(user_id))
        if user:
            # Ensure user is attached to current session
            db.session.add(user)
            db.session.refresh(user)
        return user
    except Exception as e:
        logger.error(f"Error loading user {user_id}: {e}")
        db.session.rollback()
        return None

def create_access_token(data: dict, expires_delta: timedelta = None):
    """Create JWT access token with expiration, issued-at, and JWT ID"""
    try:
        now = datetime.utcnow()
        to_encode = data.copy()
        
        # Set expiration
        if expires_delta:
            expire = now + expires_delta
        else:
            # Default to 1 hour, or use config
            expiration_seconds = current_app.config.get('JWT_EXPIRATION', 3600)
            expire = now + timedelta(seconds=expiration_seconds)
        
        # Add standard JWT claims
        to_encode.update({
            "exp": expire,
            "iat": now,
            "jti": str(uuid.uuid4())  # JWT ID for token tracking
        })
        
        # Ensure 'sub' field is a string if present
        if 'sub' in to_encode and not isinstance(to_encode['sub'], str):
            to_encode['sub'] = str(to_encode['sub'])
        
        secret_key = current_app.config.get('JWT_SECRET_KEY')
        if not secret_key:
            raise ValueError("JWT_SECRET_KEY not configured")
            
        encoded_jwt = jwt.encode(to_encode, secret_key, algorithm="HS256")
        return encoded_jwt
    except Exception as e:
        logger.error(f"JWT creation error: {e}")
        return None

def verify_token(token: str):
    """Verify JWT token with expiration check"""
    try:
        secret_key = current_app.config.get('JWT_SECRET_KEY')
        if not secret_key:
            logger.warning("JWT_SECRET_KEY not configured in verify_token")
            return None
            
        if not token:
            logger.warning("No token provided to verify_token")
            return None
        
        # Decode and verify token (includes expiration check)
        payload = jwt.decode(
            token, 
            secret_key, 
            algorithms=["HS256"],
            options={"verify_exp": True}  # Explicitly verify expiration
        )
        
        user_id = payload.get("sub")
        if user_id is None:
            logger.warning(f"No 'sub' field in JWT payload: {payload}")
            return None
        
        # Convert back to int if it's a string
        try:
            return int(user_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid user_id format in JWT: {user_id}")
            return None
    except jwt.ExpiredSignatureError:
        logger.warning("Expired JWT token attempted")
        return None
    except JWTError as e:
        logger.warning(f"JWT verification error: {e}")
        return None

def authenticate_user(username: str, password: str):
    """Authenticate user with username and password"""
    try:
        user = User.query.filter_by(username=username).first()
        
        if not user:
            return None
        
        # Ensure user is attached to current session
        db.session.add(user)
        db.session.refresh(user)
        
        # Check if account is locked
        if user.is_locked():
            return None
        
        # Check if account is active
        if not user.is_active:
            return None
        
        # Verify password
        if not user.check_password(password):
            # Increment failed attempts
            user.failed_login_attempts += 1
            
            # Lock account if max attempts reached
            if user.failed_login_attempts >= current_app.config['MAX_LOGIN_ATTEMPTS']:
                user.locked_until = datetime.utcnow() + timedelta(seconds=current_app.config['LOCKOUT_DURATION'])
            
            try:
                db.session.commit()
            except Exception as e:
                logger.error(f"Error updating failed login attempts: {e}")
                db.session.rollback()
            return None
        
        # Reset failed attempts on successful login
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login = datetime.utcnow()
        
        try:
            db.session.commit()
        except Exception as e:
            logger.error(f"Error updating successful login: {e}")
            db.session.rollback()
        
        return user
        
    except Exception as e:
        logger.error(f"Error in authenticate_user: {e}")
        db.session.rollback()
        return None

def require_auth(f):
    """Decorator to require authentication for API endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for session-based auth first (for web interface)
        if current_user.is_authenticated:
            return f(*args, **kwargs)
        
        # Check for JWT token (for API)
        token = None
        auth_header = request.headers.get('Authorization')
        
        if auth_header:
            try:
                token = auth_header.split(' ')[1]  # Bearer <token>
            except IndexError:
                return jsonify({'error': 'Invalid authorization header format'}), 401
        
        if not token:
            return jsonify({'error': 'No authentication token provided'}), 401
        
        user_id = verify_token(token)
        if user_id is None:
            return jsonify({'error': 'Invalid or expired token'}), 401
        
        try:
            user = User.query.get(user_id)
            if user:
                # Ensure user is attached to current session
                db.session.add(user)
                db.session.refresh(user)
                
            if not user or not user.is_active:
                return jsonify({'error': 'User not found or inactive'}), 401
            
            g.current_user = user
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in require_auth: {e}")
            db.session.rollback()
            return jsonify({'error': 'Authentication error'}), 401
    
    return decorated_function

def require_admin(f):
    """Decorator to require admin privileges"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # First check authentication
        auth_result = require_auth(f)
        if hasattr(auth_result, 'status_code') and auth_result.status_code != 200:
            return auth_result
        
        # Check admin privileges
        user = current_user if current_user.is_authenticated else g.get('current_user')
        
        if not user or not user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403
        
        return f(*args, **kwargs)
    
    return decorated_function

def get_current_user():
    """Get current authenticated user"""
    if current_user.is_authenticated:
        return current_user
    return g.get('current_user')

def hash_password(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        # Invalid hash format or other errors should return False
        return False

def validate_password_complexity(password: str) -> tuple[bool, list[str]]:
    """
    Validate password meets complexity requirements
    
    Requirements:
    - Minimum 8 characters
    - At least one uppercase letter (A-Z)
    - At least one lowercase letter (a-z)
    - At least one number (0-9)
    - At least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)
    
    Returns:
        (is_valid, list_of_errors)
    """
    errors = []
    
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long")
    
    if not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter")
    
    if not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter")
    
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one number")
    
    special_chars = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    if not any(c in special_chars for c in password):
        errors.append("Password must contain at least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)")
    
    return (len(errors) == 0, errors)

def create_default_user():
    """Create default admin user if none exists"""
    if User.query.count() == 0:
        admin_user = User(
            username='admin',
            email='admin@localhost',
            is_admin=True,
            is_active=True,
            password_reset_required=True  # Force password change on first login
        )
        admin_user.set_password('admin123')  # Default password - should be changed
        db.session.add(admin_user)
        db.session.commit()
        logger.info("Default admin user created:")
        logger.info("Username: admin")
        logger.info("Password: admin123")
        logger.warning("Password reset will be required on first login!")
