"""
Database models and ORM setup using SQLAlchemy
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON, Float
from sqlalchemy.orm import relationship
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
import uuid
import json
import logging
import sqlite3

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode and busy timeout for SQLite to reduce 'database is locked' errors."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")  # 30 seconds
        cursor.close()


def init_app(app):
    """Initialize database with Flask app"""
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['DATABASE_URL']
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # SQLite: add timeout so connections wait for lock instead of failing immediately
    db_url = app.config.get('DATABASE_URL', '')
    if 'sqlite' in db_url:
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'connect_args': {'timeout': 30},
        }

    db.init_app(app)
    
    with app.app_context():
        db.create_all()
        
        # Migrate: Add password_reset_required column if it doesn't exist
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('users')]
            
            if 'password_reset_required' not in columns:
                logger = logging.getLogger(__name__)
                logger.info("Adding password_reset_required column to users table...")
                db.session.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN password_reset_required BOOLEAN DEFAULT 1 NOT NULL
                """))
                db.session.commit()
                logger.info("✅ Added password_reset_required column")
                
                # Set password_reset_required=True for all existing users
                users = User.query.all()
                for user in users:
                    if not hasattr(user, 'password_reset_required') or user.password_reset_required is None:
                        user.password_reset_required = True
                db.session.commit()
                logger.info(f"✅ Updated {len(users)} users to require password reset")
        except Exception as e:
            # If migration fails, log but don't crash (column might already exist)
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not migrate password_reset_required column: {e}")
            db.session.rollback()
        
        # Migrate: Create call_jobs table if it doesn't exist
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'call_jobs' not in tables:
                logger = logging.getLogger(__name__)
                logger.info("Creating call_jobs table...")
                db.session.execute(text("""
                    CREATE TABLE call_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workflow_id INTEGER,
                        campaign_id INTEGER NOT NULL,
                        target_phone VARCHAR(50) NOT NULL,
                        target_data TEXT,
                        call_content TEXT,
                        status VARCHAR(50) DEFAULT 'pending',
                        contact_id VARCHAR(100),
                        completed_at DATETIME,
                        error_message TEXT,
                        retry_count INTEGER DEFAULT 0,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (workflow_id) REFERENCES workflows(id),
                        FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
                    )
                """))
                db.session.commit()
                logger.info("✅ Created call_jobs table")
        except Exception as e:
            # If migration fails, log but don't crash (table might already exist)
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not create call_jobs table: {e}")
            db.session.rollback()

        # Migrate: Add pre_render_config to sending_workflows if it doesn't exist
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            if 'sending_workflows' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('sending_workflows')]
                if 'pre_render_config' not in columns:
                    logger = logging.getLogger(__name__)
                    logger.info("Adding pre_render_config column to sending_workflows...")
                    db.session.execute(text("""
                        ALTER TABLE sending_workflows
                        ADD COLUMN pre_render_config TEXT
                    """))
                    db.session.commit()
                    logger.info("Added pre_render_config column")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not migrate pre_render_config column: {e}")
            db.session.rollback()

        # Migrate: Add workflow_id to sending_workflows if it doesn't exist
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            if 'sending_workflows' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('sending_workflows')]
                if 'workflow_id' not in columns:
                    logger = logging.getLogger(__name__)
                    logger.info("Adding workflow_id column to sending_workflows...")
                    db.session.execute(text("""
                        ALTER TABLE sending_workflows
                        ADD COLUMN workflow_id INTEGER REFERENCES workflows(id)
                    """))
                    db.session.commit()
                    logger.info("Added workflow_id column")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not migrate workflow_id column: {e}")
            db.session.rollback()

        # Migrate: Add proxy_mode column to credential_proxy_jobs if it doesn't exist
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            if 'credential_proxy_jobs' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('credential_proxy_jobs')]
                if 'proxy_mode' not in columns:
                    logger = logging.getLogger(__name__)
                    logger.info("Adding proxy_mode column to credential_proxy_jobs...")
                    db.session.execute(text("ALTER TABLE credential_proxy_jobs ADD COLUMN proxy_mode VARCHAR(20) DEFAULT 'async'"))
                    db.session.commit()
                    logger.info("Added proxy_mode column")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not migrate proxy_mode column: {e}")
            db.session.rollback()

        # Migrate: Add gate token fields to campaigns if they don't exist
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            if 'campaigns' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('campaigns')]
                logger = logging.getLogger(__name__)
                gate_columns = {
                    'gate_enabled': "ALTER TABLE campaigns ADD COLUMN gate_enabled BOOLEAN DEFAULT 0",
                    'gate_token': "ALTER TABLE campaigns ADD COLUMN gate_token VARCHAR(50)",
                    'gate_param_name': "ALTER TABLE campaigns ADD COLUMN gate_param_name VARCHAR(50) DEFAULT 'rid'",
                    'gate_mode': "ALTER TABLE campaigns ADD COLUMN gate_mode VARCHAR(20) DEFAULT 'template'",
                    'gate_redirect_url': "ALTER TABLE campaigns ADD COLUMN gate_redirect_url VARCHAR(500)",
                    'gate_template_id': "ALTER TABLE campaigns ADD COLUMN gate_template_id INTEGER REFERENCES templates(id)",
                    'is_mms_enabled': "ALTER TABLE campaigns ADD COLUMN is_mms_enabled BOOLEAN DEFAULT 0",
                }
                for col_name, alter_sql in gate_columns.items():
                    if col_name not in columns:
                        logger.info(f"Adding {col_name} column to campaigns...")
                        db.session.execute(text(alter_sql))
                        db.session.commit()
                        logger.info(f"Added {col_name} column")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not migrate gate token columns: {e}")
            db.session.rollback()

        # Migrate: Add phone column to tracked_users if it doesn't exist
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            if 'tracked_users' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('tracked_users')]
                if 'phone' not in columns:
                    logger = logging.getLogger(__name__)
                    logger.info("Adding phone column to tracked_users...")
                    db.session.execute(text("ALTER TABLE tracked_users ADD COLUMN phone VARCHAR(50)"))
                    db.session.commit()
                    logger.info("Added phone column to tracked_users")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not migrate phone column: {e}")
            db.session.rollback()

        # Migrate: Add render_uuid + mms_card_config_id columns to sms_jobs
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            if 'sms_jobs' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('sms_jobs')]
                logger = logging.getLogger(__name__)
                if 'render_uuid' not in columns:
                    logger.info("Adding render_uuid column to sms_jobs...")
                    db.session.execute(text("ALTER TABLE sms_jobs ADD COLUMN render_uuid VARCHAR(36)"))
                    db.session.commit()
                    logger.info("Added render_uuid column to sms_jobs")
                if 'mms_card_config_id' not in columns:
                    logger.info("Adding mms_card_config_id column to sms_jobs...")
                    db.session.execute(text("ALTER TABLE sms_jobs ADD COLUMN mms_card_config_id VARCHAR(100)"))
                    db.session.commit()
                    logger.info("Added mms_card_config_id column to sms_jobs")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not migrate sms_jobs columns: {e}")
            db.session.rollback()

class User(UserMixin, db.Model):
    """User model for authentication"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime)
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime)
    password_reset_required = Column(Boolean, default=True)  # Force password change on first login

    def get_id(self):
        """Required by Flask-Login"""
        return str(self.id)

    def is_authenticated(self):
        """Required by Flask-Login"""
        return True

    def is_anonymous(self):
        """Required by Flask-Login"""
        return False
    
    def set_password(self, password):
        """Set password hash"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check password"""
        return check_password_hash(self.password_hash, password)
    
    def is_locked(self):
        """Check if account is locked"""
        if self.locked_until and self.locked_until > datetime.utcnow():
            return True
        return False
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'is_active': self.is_active,
            'is_admin': self.is_admin,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }
    
    # Relationships
    notifications = relationship('Notification', backref='user', cascade='all, delete-orphan')

class Notification(db.Model):
    """Notification model for user notifications"""
    __tablename__ = 'notifications'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    message = Column(Text, nullable=False)
    type = Column(String(20), nullable=False)  # 'success', 'error', 'warning', 'info'
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)
    extra_data = Column(JSON, default=dict)  # Optional extra data for future use (action URLs, etc.)
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'message': self.message,
            'type': self.type,
            'is_read': self.is_read,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'read_at': self.read_at.isoformat() if self.read_at else None,
            'metadata': self.extra_data or {}  # Keep 'metadata' key in API response for compatibility
        }

class Template(db.Model):
    """Template library model"""
    __tablename__ = 'templates'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    category = Column(String(50))  # login, office365, gmail, etc.
    template_type = Column(String(20), default='main')  # main, captcha, error
    template_html = Column(Text, nullable=False)
    preview_image = Column(String(500))  # Path to preview image
    is_public = Column(Boolean, default=True)
    variables = Column(JSON, default=list)  # Template variable definitions
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey('users.id'))
    
    # Phishing detection fields
    phishing_score = Column(Float, nullable=True)  # Detection confidence score
    phishing_detected_at = Column(DateTime, nullable=True)  # Last detection timestamp
    phishing_is_phishing = Column(Boolean, nullable=True)  # Binary classification result
    phishing_override = Column(Boolean, default=False)  # User override flag
    phishing_approved_by_id = Column(Integer, ForeignKey('users.id'), nullable=True)  # Who approved override
    phishing_approved_at = Column(DateTime, nullable=True)  # When override was approved
    
    # Relationships
    created_by = relationship('User', foreign_keys=[created_by_id], backref='templates')
    phishing_approved_by = relationship('User', foreign_keys=[phishing_approved_by_id], backref='approved_templates')
    
    def to_dict(self, include_content=True):
        """Convert to dictionary"""
        data = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'category': self.category,
            'template_type': self.template_type,
            'preview_image': self.preview_image,
            'is_public': self.is_public,
            'variables': self.variables or [],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by': self.created_by.username if self.created_by else None,
            'phishing_score': self.phishing_score,
            'phishing_detected_at': self.phishing_detected_at.isoformat() if self.phishing_detected_at else None,
            'phishing_is_phishing': self.phishing_is_phishing,
            'phishing_override': self.phishing_override,
            'phishing_approved_by': self.phishing_approved_by.username if self.phishing_approved_by else None,
            'phishing_approved_at': self.phishing_approved_at.isoformat() if self.phishing_approved_at else None
        }
        
        if include_content:
            data.update({
                'template_html': self.template_html
            })
        
        return data

class Campaign(db.Model):
    """Campaign model"""
    __tablename__ = 'campaigns'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    campaign_type = Column(String(20), default='inbound', nullable=False)  # 'inbound' or 'outbound'
    uid = Column(String(50), unique=True, nullable=True, default=lambda: str(uuid.uuid4())[:8])  # Nullable for outbound campaigns
    template_id = Column(Integer, ForeignKey('templates.id'), nullable=True)  # Reference to main template
    captcha_template_id = Column(Integer, ForeignKey('templates.id'), nullable=True)  # Reference to CAPTCHA template (for campaign-level gate)
    captcha_enabled = Column(Boolean, default=False)  # Require CAPTCHA before landing page (inbound only)
    template_html = Column(Text, nullable=True)  # Rendered/customized HTML (optional for outbound campaigns)
    config = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), default='draft')  # draft, active, paused, completed
    
    # Workflow references
    get_workflow_id = Column(Integer, ForeignKey('workflows.id'), nullable=True)  # Workflow for GET requests
    post_workflow_id = Column(Integer, ForeignKey('workflows.id'), nullable=True)  # Workflow for POST requests
    variables = Column(JSON, nullable=False, default=dict)  # Campaign-specific variables for workflows
    
    # SSL Configuration fields
    ssl_mode = Column(String(20), default='automatic')  # automatic, custom, self_signed, disabled
    ssl_cert_path = Column(String(255), nullable=True)  # Path to certificate file
    ssl_key_path = Column(String(255), nullable=True)   # Path to private key file
    ssl_ca_path = Column(String(255), nullable=True)    # Path to CA chain file
    custom_domain = Column(String(255), nullable=True)  # Custom domain override
    caddy_config_id = Column(String(100), nullable=True)  # Caddy configuration ID
    is_mms_enabled = Column(Boolean, default=False)  # Outbound campaigns: route custom_domain via Caddy for MMS card delivery
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey('users.id'))
    
    # Phishing detection fields
    phishing_score = Column(Float, nullable=True)  # Detection confidence score
    phishing_detected_at = Column(DateTime, nullable=True)  # Last detection timestamp
    phishing_is_phishing = Column(Boolean, nullable=True)  # Binary classification result
    phishing_override = Column(Boolean, default=False)  # User override flag
    phishing_approved_by_id = Column(Integer, ForeignKey('users.id'), nullable=True)  # Who approved override
    phishing_approved_at = Column(DateTime, nullable=True)  # When override was approved
    
    # User-Agent filtering fields (inbound campaigns only)
    ua_filter_enabled = Column(Boolean, default=False)  # Enable User-Agent filtering
    ua_deny_list = Column(JSON, nullable=False, default=list)  # List of UA strings to block
    ua_blocked_template_id = Column(Integer, ForeignKey('templates.id'), nullable=True)  # Template to show when blocked

    # Gate token filtering (inbound campaigns only)
    gate_enabled = Column(Boolean, default=False)
    gate_token = Column(String(50), default=lambda: str(uuid.uuid4())[:12])
    gate_param_name = Column(String(50), default='rid')
    gate_mode = Column(String(20), default='template')  # 'template' or 'redirect'
    gate_redirect_url = Column(String(500), nullable=True)
    gate_template_id = Column(Integer, ForeignKey('templates.id'), nullable=True)
    
    # Relationships
    created_by = relationship('User', foreign_keys=[created_by_id], backref='campaigns')
    phishing_approved_by = relationship('User', foreign_keys=[phishing_approved_by_id], backref='approved_campaigns')
    template = relationship('Template', foreign_keys=[template_id], backref='main_campaigns')
    captcha_template = relationship('Template', foreign_keys=[captcha_template_id], backref='captcha_campaigns')
    ua_blocked_template = relationship('Template', foreign_keys=[ua_blocked_template_id], backref='ua_blocked_campaigns')
    gate_template = relationship('Template', foreign_keys=[gate_template_id], backref='gate_campaigns')
    get_workflow = relationship('Workflow', foreign_keys=[get_workflow_id], backref='get_campaigns')
    post_workflow = relationship('Workflow', foreign_keys=[post_workflow_id], backref='post_campaigns')
    events = relationship('Event', backref='campaign', cascade='all, delete-orphan')
    tracked_users = relationship('TrackedUser', backref='campaign', cascade='all, delete-orphan')
    
    def to_dict(self, include_template=False, include_sensitive_config=False):
        """Convert to dictionary. By default config excludes captcha_secret_key."""
        config = self.config or {}
        if not include_sensitive_config and isinstance(config, dict):
            config = {k: v for k, v in config.items() if k != 'captcha_secret_key'}
        data = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'campaign_type': self.campaign_type,
            'uid': self.uid,
            'template_id': self.template_id,
            'captcha_template_id': self.captcha_template_id,
            'captcha_enabled': getattr(self, 'captcha_enabled', False),
            'config': config,
            'status': self.status,
            'get_workflow_id': self.get_workflow_id,
            'post_workflow_id': self.post_workflow_id,
            'get_workflow_name': self.get_workflow.name if self.get_workflow else None,
            'post_workflow_name': self.post_workflow.name if self.post_workflow else None,
            'variables': self.variables,
            'ssl_mode': self.ssl_mode,
            'ssl_cert_path': self.ssl_cert_path,
            'ssl_key_path': self.ssl_key_path,
            'ssl_ca_path': self.ssl_ca_path,
            'custom_domain': self.custom_domain,
            'caddy_config_id': self.caddy_config_id,
            'is_mms_enabled': bool(self.is_mms_enabled),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by': self.created_by.username if self.created_by else None,
            'template_name': self.template.name if self.template else None,
            'captcha_template_name': self.captcha_template.name if self.captcha_template else None,
            'phishing_score': self.phishing_score,
            'phishing_detected_at': self.phishing_detected_at.isoformat() if self.phishing_detected_at else None,
            'phishing_is_phishing': self.phishing_is_phishing,
            'phishing_override': self.phishing_override,
            'phishing_approved_by': self.phishing_approved_by.username if self.phishing_approved_by else None,
            'phishing_approved_at': self.phishing_approved_at.isoformat() if self.phishing_approved_at else None,
            'gate_enabled': getattr(self, 'gate_enabled', False),
            'gate_token': getattr(self, 'gate_token', None),
            'gate_param_name': getattr(self, 'gate_param_name', 'rid'),
            'gate_mode': getattr(self, 'gate_mode', 'template'),
            'gate_redirect_url': getattr(self, 'gate_redirect_url', None),
            'gate_template_id': getattr(self, 'gate_template_id', None),
            'gate_template_name': self.gate_template.name if getattr(self, 'gate_template', None) else None,
        }

        if include_template:
            data.update({
                'template_html': self.template_html
            })
        
        return data

class Event(db.Model):
    """Event tracking model"""
    __tablename__ = 'events'
    
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=False)
    event_type = Column(String(50), nullable=False)
    data = Column(JSON)
    ip_address = Column(String(45))
    user_agent = Column(Text)
    session_id = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'campaign_id': self.campaign_id,
            'event_type': self.event_type,
            'data': self.data,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'session_id': self.session_id,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class TrackedUser(db.Model):
    """Tracked user model for email campaigns"""
    __tablename__ = 'tracked_users'
    
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=False)
    email = Column(String(255), nullable=False)
    tracking_id = Column(String(100), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    first_name = Column(String(100))
    last_name = Column(String(100))
    department = Column(String(100))
    phone = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    tracking_events = relationship('TrackingEvent', backref='tracked_user', cascade='all, delete-orphan')
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'campaign_id': self.campaign_id,
            'email': self.email,
            'tracking_id': self.tracking_id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'department': self.department,
            'phone': self.phone,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class TrackingEvent(db.Model):
    """Individual tracking events"""
    __tablename__ = 'tracking_events'
    
    id = Column(Integer, primary_key=True)
    tracked_user_id = Column(Integer, ForeignKey('tracked_users.id'), nullable=False)
    event_type = Column(String(50), nullable=False)  # email_opened, link_clicked, form_submitted, etc.
    ip_address = Column(String(45))
    user_agent = Column(Text)
    additional_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'tracked_user_id': self.tracked_user_id,
            'event_type': self.event_type,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'additional_data': self.additional_data,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class Asset(db.Model):
    """File asset model"""
    __tablename__ = 'assets'
    
    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_hash = Column(String(64), nullable=False)  # SHA-256 hash for deduplication
    mime_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    uploaded_by_id = Column(Integer, ForeignKey('users.id'))
    
    # Relationships
    uploaded_by = relationship('User', backref='uploaded_assets')
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'filename': self.filename,
            'original_filename': self.original_filename,
            'file_path': self.file_path,
            'file_hash': self.file_hash,
            'mime_type': self.mime_type,
            'file_size': self.file_size,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'uploaded_by': self.uploaded_by.username if self.uploaded_by else None
        }

class Setting(db.Model):
    """Application settings model"""
    __tablename__ = 'settings'
    
    id = Column(Integer, primary_key=True)
    key = Column(String(255), unique=True, nullable=False)
    value = Column(Text)
    value_type = Column(String(50), default='string')  # string, int, bool, json
    category = Column(String(100))  # server, security, external_services, etc.
    description = Column(Text)
    is_sensitive = Column(Boolean, default=False)  # Hide values in UI
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def get_value(self):
        """Get typed value"""
        if self.value is None:
            return None
            
        if self.value_type == 'int':
            return int(self.value)
        elif self.value_type == 'bool':
            return self.value.lower() in ('true', '1', 'yes', 'on')
        elif self.value_type == 'json':
            return json.loads(self.value)
        else:
            return self.value
    
    def set_value(self, value):
        """Set typed value"""
        if self.value_type == 'json':
            self.value = json.dumps(value)
        else:
            self.value = str(value)
        self.updated_at = datetime.utcnow()
    
    @staticmethod
    def get_setting(key, default=None):
        """Get setting value by key"""
        setting = Setting.query.filter_by(key=key).first()
        return setting.get_value() if setting else default
    
    @staticmethod
    def set_setting(key, value, value_type='string', category=None, description=None, is_sensitive=False):
        """Set or update setting"""
        setting = Setting.query.filter_by(key=key).first()
        if not setting:
            setting = Setting(
                key=key,
                value_type=value_type,
                category=category,
                description=description,
                is_sensitive=is_sensitive
            )
            db.session.add(setting)
        
        setting.set_value(value)
        db.session.commit()
        return setting
    
    def to_dict(self, include_sensitive=False):
        """Convert to dictionary"""
        value = self.get_value() if not self.is_sensitive or include_sensitive else '***'
        return {
            'id': self.id,
            'key': self.key,
            'value': value,
            'value_type': self.value_type,
            'category': self.category,
            'description': self.description,
            'is_sensitive': self.is_sensitive,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class Plugin(db.Model):
    """Plugin model for workflow system"""
    __tablename__ = 'plugins'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    plugin_type = Column(String(100), unique=True, nullable=False)
    plugin_category = Column(String(50), nullable=False)  # campaign, sending, target_selection, etc.
    code_path = Column(String(500))  # Path to plugin code file (for custom plugins)
    config_schema = Column(JSON, default=dict)
    is_builtin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey('users.id'))
    
    # Relationships
    created_by = relationship('User', backref='plugins')
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'plugin_type': self.plugin_type,
            'plugin_category': self.plugin_category,
            'code_path': self.code_path,
            'config_schema': self.config_schema or {},
            'is_builtin': self.is_builtin,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by': self.created_by.username if self.created_by else None
        }

class Workflow(db.Model):
    """Workflow model for campaign and sending workflows"""
    __tablename__ = 'workflows'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    workflow_type = Column(String(50), nullable=False)  # 'campaign' or 'sending'
    http_method = Column(String(10), nullable=False, default='BOTH')  # 'GET', 'POST', or 'BOTH'
    workflow_data = Column(JSON, nullable=False, default=dict)  # Node definitions, connections, etc.
    is_public = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey('users.id'))
    
    # Relationships
    created_by = relationship('User', backref='workflows')
    nodes = relationship('WorkflowNode', backref='workflow', cascade='all, delete-orphan')
    executions = relationship('WorkflowExecution', backref='workflow', cascade='all, delete-orphan')
    
    def to_dict(self, include_data=True):
        """Convert to dictionary"""
        data = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'workflow_type': self.workflow_type,
            'http_method': self.http_method,
            'is_public': self.is_public,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by': self.created_by.username if self.created_by else None,
            'node_count': len(self.nodes) if self.nodes else 0
        }
        
        if include_data:
            data['workflow_data'] = self.workflow_data
        
        return data
    
    def export_data(self):
        """Export workflow configuration as JSON-serializable dict"""
        return {
            'name': self.name,
            'description': self.description,
            'workflow_type': self.workflow_type,
            'http_method': self.http_method,
            'workflow_data': self.workflow_data,
            'is_public': self.is_public,
            'nodes': [node.to_dict() for node in self.nodes]
        }
    
    @classmethod
    def import_from_data(cls, data, user_id):
        """
        Create a new workflow from exported data
        
        Args:
            data: Workflow data dictionary from export
            user_id: ID of user creating the workflow
            
        Returns:
            New Workflow instance (not yet committed)
        """
        workflow = cls(
            name=data.get('name', 'Imported Workflow'),
            description=data.get('description'),
            workflow_type=data.get('workflow_type', 'campaign'),
            http_method=data.get('http_method', 'BOTH'),
            workflow_data=data.get('workflow_data', {}),
            is_public=data.get('is_public', True),
            is_active=True,
            created_by_id=user_id
        )
        
        # Import nodes if provided
        if 'nodes' in data:
            from shared.database import WorkflowNode
            for node_data in data['nodes']:
                node = WorkflowNode(
                    workflow=workflow,
                    node_id=node_data.get('node_id'),
                    node_type=node_data.get('node_type', 'plugin'),
                    plugin_id=node_data.get('plugin_id'),
                    plugin_type=node_data.get('plugin_type'),
                    position_x=node_data.get('position_x', 0),
                    position_y=node_data.get('position_y', 0),
                    config=node_data.get('config', {}),
                    connections=node_data.get('connections', [])
                )
                workflow.nodes.append(node)
        
        return workflow

class WorkflowNode(db.Model):
    """Workflow node model - represents a node in a workflow"""
    __tablename__ = 'workflow_nodes'
    
    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey('workflows.id'), nullable=False)
    node_id = Column(String(100), nullable=False)  # Unique ID within workflow
    node_type = Column(String(50), nullable=False)  # 'plugin', 'start', 'end', 'condition'
    plugin_id = Column(Integer, ForeignKey('plugins.id'), nullable=True)
    plugin_type = Column(String(100), nullable=True)  # Cached plugin type for faster lookup
    position_x = Column(Integer, default=0)
    position_y = Column(Integer, default=0)
    config = Column(JSON, default=dict)  # Plugin-specific configuration
    connections = Column(JSON, default=list)  # List of connected node IDs
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    plugin = relationship('Plugin', backref='workflow_nodes')
    
    def to_dict(self):
        """Convert to dictionary"""
        # Handle both old format (list of strings) and new format (list of dicts with path/target)
        connections = self.connections or []
        # Ensure connections is always a list for backward compatibility
        if not isinstance(connections, list):
            connections = []
        
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'node_id': self.node_id,
            'node_type': self.node_type,
            'plugin_id': self.plugin_id,
            'plugin_type': self.plugin_type,
            'position_x': self.position_x,
            'position_y': self.position_y,
            'config': self.config or {},
            'connections': connections
        }

class WorkflowExecution(db.Model):
    """Workflow execution tracking model"""
    __tablename__ = 'workflow_executions'
    
    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey('workflows.id'), nullable=False)
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=True)
    execution_context = Column(JSON, default=dict)  # Context at execution start
    status = Column(String(50), default='pending')  # pending, running, completed, failed
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    campaign = relationship('Campaign', backref='workflow_executions')
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'campaign_id': self.campaign_id,
            'status': self.status,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class SendingWorkflow(db.Model):
    """Sending workflow model for email campaigns"""
    __tablename__ = 'sending_workflows'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=True)  # Nullable when created from builder until campaign is set
    workflow_id = Column(Integer, ForeignKey('workflows.id'), nullable=True)  # Node-based graph; when set, execution uses workflow engine
    target_selection_config = Column(JSON, default=dict)  # Target selection plugin config
    sending_method_config = Column(JSON, default=dict)  # Email sending plugin config
    template_validation_config = Column(JSON, default=dict)  # Email validation plugin config
    pre_render_config = Column(JSON, default=dict)  # Optional plugin run per target before render (e.g. url_obfuscator)
    rate_limit_config = Column(JSON, default=dict)  # Rate limiting configuration
    retry_config = Column(JSON, default=dict)  # Retry configuration
    status = Column(String(50), default='draft')  # draft, scheduled, running, completed, failed
    scheduled_at = Column(DateTime)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    cancelled = Column(Boolean, default=False)  # Cancellation flag
    progress = Column(JSON, default=dict)  # Progress tracking (current, total, percentage)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey('users.id'))
    
    # Relationships
    campaign = relationship('Campaign', backref='sending_workflows')
    workflow = relationship('Workflow', backref='sending_workflow_runs', foreign_keys=[workflow_id])
    created_by = relationship('User', backref='created_sending_workflows')
    email_jobs = relationship('EmailJob', backref='sending_workflow', cascade='all, delete-orphan')
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'campaign_id': self.campaign_id,
            'campaign_name': self.campaign.name if self.campaign else None,
            'workflow_id': self.workflow_id,
            'workflow_name': self.workflow.name if self.workflow else None,
            'target_selection_config': self.target_selection_config or {},
            'sending_method_config': self.sending_method_config or {},
            'template_validation_config': self.template_validation_config or {},
            'pre_render_config': self.pre_render_config or {},
            'rate_limit_config': self.rate_limit_config or {},
            'retry_config': self.retry_config or {},
            'status': self.status,
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'cancelled': self.cancelled,
            'progress': self.progress or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by': self.created_by.username if self.created_by else None
        }

class EmailJob(db.Model):
    """Email job model for tracking individual email sends"""
    __tablename__ = 'email_jobs'
    
    id = Column(Integer, primary_key=True)
    sending_workflow_id = Column(Integer, ForeignKey('sending_workflows.id'), nullable=False)
    target_email = Column(String(255), nullable=False)
    target_data = Column(JSON, default=dict)  # Target-specific data (name, custom fields, etc.)
    email_content = Column(JSON, default=dict)  # Rendered email content
    status = Column(String(50), default='pending')  # pending, sent, failed, retrying
    sent_at = Column(DateTime)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'sending_workflow_id': self.sending_workflow_id,
            'target_email': self.target_email,
            'target_data': self.target_data or {},
            'status': self.status,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'error_message': self.error_message,
            'retry_count': self.retry_count,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class CallJob(db.Model):
    """Call job model for tracking individual phone calls"""
    __tablename__ = 'call_jobs'
    
    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey('workflows.id'), nullable=True)
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=False)
    target_phone = Column(String(50), nullable=False)
    target_data = Column(JSON, default=dict)  # Target-specific data (name, company, etc.)
    call_content = Column(JSON, default=dict)  # Call content (device code, message, etc.)
    status = Column(String(50), default='pending')  # pending, completed, failed, retrying
    contact_id = Column(String(100))  # AWS Connect contact ID
    completed_at = Column(DateTime)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    campaign = relationship('Campaign', backref='call_jobs')
    workflow = relationship('Workflow', backref='call_jobs')
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'campaign_id': self.campaign_id,
            'target_phone': self.target_phone,
            'target_data': self.target_data or {},
            'call_content': self.call_content or {},
            'status': self.status,
            'contact_id': self.contact_id,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error_message': self.error_message,
            'retry_count': self.retry_count,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class SmsJob(db.Model):
    """SMS job model for tracking individual SMS sends"""
    __tablename__ = 'sms_jobs'

    id = Column(Integer, primary_key=True)
    render_uuid = Column(String(36), unique=True, index=True)  # UUID for MMS card rendering (keeps target data out of URLs)
    mms_card_config_id = Column(String(100))  # Which MMS card template to use for rendering
    workflow_id = Column(Integer, ForeignKey('workflows.id'), nullable=True)
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=False)
    target_phone = Column(String(50), nullable=False)
    target_data = Column(JSON, default=dict)  # Target-specific data (name, custom fields, etc.)
    message_content = Column(JSON, default=dict)  # Message body, from number, etc.
    status = Column(String(50), default='pending')  # pending, sent, delivered, failed, undelivered
    message_sid = Column(String(100))  # Twilio Message SID
    sent_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    campaign = relationship('Campaign', backref='sms_jobs')
    workflow = relationship('Workflow', backref='sms_jobs')

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'campaign_id': self.campaign_id,
            'target_phone': self.target_phone,
            'target_data': self.target_data or {},
            'message_content': self.message_content or {},
            'status': self.status,
            'message_sid': self.message_sid,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error_message': self.error_message,
            'retry_count': self.retry_count,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class CredentialProxyJob(db.Model):
    """Credential proxy job model for queue management"""
    __tablename__ = 'credential_proxy_jobs'
    
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=False)
    credentials = Column(JSON, nullable=False)  # Captured credentials
    target_sites = Column(JSON, nullable=False)  # Array of target site configs
    status = Column(String(50), default='pending')  # pending, processing, completed, failed
    results = Column(JSON, default=dict)  # Results from each target site
    error_message = Column(Text)
    ai_config = Column(JSON, default=dict)  # AI configuration
    browser_config = Column(JSON, default=dict)  # Browser automation configuration
    automation_timeout = Column(Integer, default=60)  # Timeout per site in seconds
    max_retries = Column(Integer, default=2)  # Maximum retries
    retry_count = Column(Integer, default=0)  # Current retry count
    proxy_mode = Column(String(20), default='async')  # 'async' or 'sync'
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    
    # Relationships
    campaign = relationship('Campaign', backref='credential_proxy_jobs')
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'campaign_id': self.campaign_id,
            'campaign_name': self.campaign.name if self.campaign else None,
            'credentials': self.credentials or {},
            'target_sites': self.target_sites or [],
            'status': self.status,
            'results': self.results or {},
            'error_message': self.error_message,
            'ai_config': self.ai_config or {},
            'browser_config': self.browser_config or {},
            'automation_timeout': self.automation_timeout,
            'max_retries': self.max_retries,
            'retry_count': self.retry_count,
            'proxy_mode': self.proxy_mode or 'async',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }
