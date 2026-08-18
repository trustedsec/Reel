"""
Business logic services for API operations
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, date
from pathlib import Path
from shared.database import db, Campaign, User, Template, Event, TrackedUser, EmailJob, CallJob, SendingWorkflow
from api.models.models import CampaignCreateRequest, CampaignUpdateRequest, TrackedUserRequest, TemplateCreateRequest, TemplateUpdateRequest
from shared.phishing_detector_service import detect_phishing
from jinja2 import Environment, BaseLoader, TemplateSyntaxError, meta, ChainableUndefined
from jinja2.sandbox import SandboxedEnvironment
import uuid
import logging
import re
import json
import shutil

logger = logging.getLogger(__name__)

class CampaignService:
    """Service for campaign operations"""
    
    def create_campaign(self, request: CampaignCreateRequest, user: User) -> Campaign:
        """Create a new campaign"""
        try:
            campaign_type = request.campaign_type or 'inbound'
            
            # Handle template selection or custom HTML
            template_html = request.template_html
            template_id = request.template_id
            
            if request.template_id:
                # Use existing template
                template = Template.query.get_or_404(request.template_id)
                # Check access permissions
                if not template.is_public and template.created_by_id != user.id:
                    raise ValueError("Access denied to template")
                
                template_html = template.template_html
            
            # Validate workflows exist
            from shared.database import Workflow
            if campaign_type == 'inbound':
                # Inbound campaigns can have GET and/or POST workflows
                if request.get_workflow_id:
                    get_workflow = Workflow.query.get(request.get_workflow_id)
                    if not get_workflow:
                        raise ValueError(f"Workflow {request.get_workflow_id} not found")
                    if get_workflow.http_method not in ('GET', 'BOTH'):
                        raise ValueError(f"Workflow {request.get_workflow_id} does not support GET requests")
                
                if request.post_workflow_id:
                    post_workflow = Workflow.query.get(request.post_workflow_id)
                    if not post_workflow:
                        raise ValueError(f"Workflow {request.post_workflow_id} not found")
                    if post_workflow.http_method not in ('POST', 'BOTH'):
                        raise ValueError(f"Workflow {request.post_workflow_id} does not support POST requests")
            else:
                # Outbound campaigns only have one workflow (in post_workflow_id)
                if request.get_workflow_id:
                    raise ValueError("Outbound campaigns can only have one workflow (use post_workflow_id, not get_workflow_id)")
                if request.post_workflow_id:
                    post_workflow = Workflow.query.get(request.post_workflow_id)
                    if not post_workflow:
                        raise ValueError(f"Workflow {request.post_workflow_id} not found")
            
            # Generate UID only for inbound campaigns
            uid = None
            if campaign_type == 'inbound':
                uid = self._generate_unique_uid()
            
            campaign = Campaign(
                name=request.name,
                description=request.description,
                campaign_type=campaign_type,
                get_workflow_id=request.get_workflow_id if campaign_type == 'inbound' else None,
                post_workflow_id=request.post_workflow_id,
                template_id=template_id,
                template_html=template_html,  # Can be None for outbound campaigns
                config=request.config,
                variables=request.variables,
                ssl_mode=request.ssl_mode or ('automatic' if campaign_type == 'inbound' else None),
                custom_domain=request.custom_domain if (campaign_type == 'inbound' or (campaign_type == 'outbound' and request.is_mms_enabled)) else None,
                created_by_id=user.id,
                uid=uid
            )

            # User-Agent filtering and CAPTCHA (inbound campaigns only)
            if campaign_type == 'inbound':
                if hasattr(Campaign, 'ua_filter_enabled'):
                    campaign.ua_filter_enabled = request.ua_filter_enabled if request.ua_filter_enabled is not None else False
                    campaign.ua_deny_list = request.ua_deny_list or []
                    campaign.ua_blocked_template_id = request.ua_blocked_template_id
                if hasattr(Campaign, 'captcha_enabled'):
                    campaign.captcha_enabled = request.captcha_enabled if request.captcha_enabled is not None else False
                    campaign.captcha_template_id = (
                        request.captcha_template_id
                        if campaign.captcha_enabled and request.captcha_template_id is not None
                        else None
                    )
                if hasattr(Campaign, 'gate_enabled'):
                    campaign.gate_enabled = request.gate_enabled if request.gate_enabled is not None else False
                    campaign.gate_token = request.gate_token
                    campaign.gate_param_name = request.gate_param_name or 'rid'
                    campaign.gate_mode = request.gate_mode or 'template'
                    campaign.gate_redirect_url = request.gate_redirect_url
                    campaign.gate_template_id = request.gate_template_id if campaign.gate_enabled else None

            # Outbound MMS toggle
            if campaign_type == 'outbound' and hasattr(Campaign, 'is_mms_enabled'):
                campaign.is_mms_enabled = bool(request.is_mms_enabled)

            db.session.add(campaign)
            db.session.flush()  # Get campaign ID
            
            # Run phishing detection only for inbound campaigns with templates
            if campaign_type == 'inbound' and template_html:
                self._run_phishing_detection(campaign)
            
            db.session.commit()
            
            logger.info(f"Campaign created: {campaign.name} (ID: {campaign.id}, Type: {campaign_type}) by user {user.username}")
            return campaign
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to create campaign: {e}")
            raise
    
    def update_campaign(self, campaign: Campaign, request: CampaignUpdateRequest) -> Campaign:
        """Update an existing campaign"""
        try:
            old_status = campaign.status
            type_changed = False
            if request.campaign_type is not None:
                if campaign.campaign_type != request.campaign_type:
                    # Prevent changing type if campaign has active Caddy config (inbound -> outbound)
                    if campaign.campaign_type == 'inbound' and request.campaign_type == 'outbound':
                        if campaign.caddy_config_id:
                            raise ValueError("Cannot change inbound campaign to outbound: Caddy configuration exists. Remove Caddy config first.")
                    type_changed = True
                    campaign.campaign_type = request.campaign_type
            
            # Determine current campaign type (may have changed above)
            current_type = campaign.campaign_type
            
            if request.name is not None:
                campaign.name = request.name
            if request.description is not None:
                campaign.description = request.description
            if request.uid is not None:
                # Only allow UID for inbound campaigns
                if current_type == 'inbound':
                    # Validate UID is unique (excluding current campaign)
                    existing_campaign = Campaign.query.filter(
                        Campaign.uid == request.uid,
                        Campaign.id != campaign.id
                    ).first()
                    if existing_campaign:
                        raise ValueError(f"Campaign UID '{request.uid}' already exists")
                    campaign.uid = request.uid
                else:
                    raise ValueError("UID can only be set for inbound campaigns")
            elif type_changed and current_type == 'outbound':
                # Clear UID when changing to outbound
                campaign.uid = None
            
            # Workflow updates based on campaign type
            from shared.database import Workflow
            if current_type == 'inbound':
                # Inbound campaigns can have GET and/or POST workflows
                # Validate and update GET workflow
                if request.get_workflow_id is not None:
                    get_workflow = Workflow.query.get(request.get_workflow_id)
                    if not get_workflow:
                        raise ValueError(f"Workflow {request.get_workflow_id} not found")
                    if get_workflow.http_method not in ('GET', 'BOTH'):
                        raise ValueError(f"Workflow {request.get_workflow_id} does not support GET requests")
                    campaign.get_workflow_id = request.get_workflow_id
                elif request.get_workflow_id is None and 'get_workflow_id' in request.dict(exclude_unset=True):
                    # Explicitly set to None if provided in request
                    campaign.get_workflow_id = None
                
                # Validate and update POST workflow
                if request.post_workflow_id is not None:
                    post_workflow = Workflow.query.get(request.post_workflow_id)
                    if not post_workflow:
                        raise ValueError(f"Workflow {request.post_workflow_id} not found")
                    if post_workflow.http_method not in ('POST', 'BOTH'):
                        raise ValueError(f"Workflow {request.post_workflow_id} does not support POST requests")
                    campaign.post_workflow_id = request.post_workflow_id
                elif request.post_workflow_id is None and 'post_workflow_id' in request.dict(exclude_unset=True):
                    # Explicitly set to None if provided in request
                    campaign.post_workflow_id = None
            else:
                # Outbound campaigns only have one workflow (in post_workflow_id)
                if request.get_workflow_id is not None:
                    raise ValueError("Outbound campaigns can only have one workflow (use post_workflow_id, not get_workflow_id)")
                campaign.get_workflow_id = None  # Always clear GET workflow for outbound
                if request.post_workflow_id is not None:
                    post_workflow = Workflow.query.get(request.post_workflow_id)
                    if not post_workflow:
                        raise ValueError(f"Workflow {request.post_workflow_id} not found")
                    campaign.post_workflow_id = request.post_workflow_id
                elif request.post_workflow_id is None and type_changed:
                    # If changing to outbound and no workflow provided, clear it
                    campaign.post_workflow_id = None
            
            # Workflow-defined: clear campaign template so workflow provides it (e.g. Render Template node)
            html_changed = False
            if getattr(request, 'template_source', None) == 'workflow_defined' and current_type == 'inbound':
                campaign.template_id = None
                campaign.template_html = None
            else:
                if request.template_id is not None:
                    campaign.template_id = request.template_id
                    # If switching to a different template, update HTML/CSS/JS
                    if request.template_id:
                        template = Template.query.get_or_404(request.template_id)
                        campaign.template_html = template.template_html
                html_changed = False
                if request.template_html is not None:
                    if campaign.template_html != request.template_html:
                        html_changed = True
                        # Reset override when HTML changes
                        campaign.phishing_override = False
                        campaign.phishing_approved_by_id = None
                        campaign.phishing_approved_at = None
                    campaign.template_html = request.template_html
            if request.config is not None:
                campaign.config = request.config
            if request.variables is not None:
                campaign.variables = request.variables
            if request.status is not None:
                campaign.status = request.status
            
            # SSL and domain - inbound, or outbound with MMS enabled
            if current_type == 'inbound':
                if request.ssl_mode is not None:
                    campaign.ssl_mode = request.ssl_mode
                if request.custom_domain is not None:
                    campaign.custom_domain = request.custom_domain
            elif current_type == 'outbound':
                # Outbound: set is_mms_enabled and conditionally accept custom_domain
                if hasattr(Campaign, 'is_mms_enabled') and request.is_mms_enabled is not None:
                    campaign.is_mms_enabled = bool(request.is_mms_enabled)
                if getattr(campaign, 'is_mms_enabled', False):
                    if request.custom_domain is not None:
                        campaign.custom_domain = request.custom_domain
                else:
                    # MMS off — clear domain and Caddy config
                    campaign.custom_domain = None
                    campaign.caddy_config_id = None
                if type_changed:
                    # Coming from inbound — clear inbound-only SSL fields
                    campaign.ssl_mode = None

            # CAPTCHA (campaign-level gate) - only for inbound campaigns
            if hasattr(Campaign, 'captcha_enabled'):
                if current_type == 'inbound':
                    if request.captcha_enabled is not None:
                        campaign.captcha_enabled = request.captcha_enabled
                    if request.captcha_template_id is not None:
                        campaign.captcha_template_id = request.captcha_template_id
                    if request.captcha_enabled is False:
                        campaign.captcha_template_id = None
                else:
                    campaign.captcha_enabled = False
                    campaign.captcha_template_id = None

            # User-Agent filtering - only for inbound campaigns
            if hasattr(Campaign, 'ua_filter_enabled'):
                if current_type == 'inbound':
                    if request.ua_filter_enabled is not None:
                        campaign.ua_filter_enabled = request.ua_filter_enabled
                    if request.ua_deny_list is not None:
                        campaign.ua_deny_list = request.ua_deny_list or []
                    if request.ua_blocked_template_id is not None:
                        campaign.ua_blocked_template_id = request.ua_blocked_template_id
                else:
                    campaign.ua_filter_enabled = False
                    campaign.ua_deny_list = []
                    campaign.ua_blocked_template_id = None

            # Gate token filtering - only for inbound campaigns
            if hasattr(Campaign, 'gate_enabled'):
                if current_type == 'inbound':
                    if request.gate_enabled is not None:
                        campaign.gate_enabled = request.gate_enabled
                    if request.gate_token is not None:
                        campaign.gate_token = request.gate_token
                    if request.gate_param_name is not None:
                        campaign.gate_param_name = request.gate_param_name
                    if request.gate_mode is not None:
                        campaign.gate_mode = request.gate_mode
                    if request.gate_redirect_url is not None:
                        campaign.gate_redirect_url = request.gate_redirect_url
                    if request.gate_template_id is not None:
                        campaign.gate_template_id = request.gate_template_id
                    if request.gate_enabled is False:
                        campaign.gate_template_id = None
                        campaign.gate_redirect_url = None

                    # Validate gate configuration is complete when enabled
                    if campaign.gate_enabled:
                        gate_mode = campaign.gate_mode or 'template'
                        if gate_mode == 'template' and not campaign.gate_template_id:
                            raise ValueError('Gate template is required when gate mode is "template"')
                        if gate_mode == 'redirect' and not campaign.gate_redirect_url:
                            raise ValueError('Redirect URL is required when gate mode is "redirect"')
                else:
                    campaign.gate_enabled = False
                    campaign.gate_token = None
                    campaign.gate_template_id = None

            campaign.updated_at = datetime.utcnow()

            # Handle status changes: validate, deploy Caddy, or remove Caddy
            # Caddy routing applies to inbound campaigns and outbound campaigns with MMS enabled.
            needs_caddy = current_type == 'inbound' or (
                current_type == 'outbound' and getattr(campaign, 'is_mms_enabled', False)
            )
            new_status = campaign.status if request.status is not None else old_status
            if request.status is not None:
                if new_status == 'active' and needs_caddy and old_status != 'active':
                    from shared.campaign_validation import validate_campaign_for_activation
                    from shared.caddy import caddy_manager, CaddySSLError, CaddyAPIError

                    if current_type == 'inbound':
                        validation_errors = validate_campaign_for_activation(campaign)
                        if validation_errors:
                            raise ValueError(
                                "Campaign validation failed: " + "; ".join(validation_errors)
                            )

                    db.session.flush()  # Ensure campaign is in DB for _build_servers_from_db
                    try:
                        if campaign.ssl_mode == 'self_signed' and campaign.custom_domain:
                            ssl_files = caddy_manager.generate_self_signed_certificate(
                                campaign.uid or campaign.custom_domain,
                                campaign.custom_domain
                            )
                            campaign.ssl_cert_path = ssl_files['cert_path']
                            campaign.ssl_key_path = ssl_files['key_path']

                        caddy_manager.deploy_campaign_config(campaign)
                        campaign.caddy_config_id = f"campaign_{campaign.uid or campaign.custom_domain}"
                    except CaddySSLError as e:
                        logger.warning(f"Caddy SSL error for campaign {campaign.uid}: {e}")
                    except CaddyAPIError as e:
                        logger.warning(f"Caddy API error for campaign {campaign.uid}: {e}")

                elif new_status in ['paused', 'completed'] and old_status == 'active' and needs_caddy:
                    from shared.caddy import caddy_manager

                    try:
                        # Outbound MMS campaigns don't have a uid; rebuild config to drop the route
                        if campaign.uid:
                            caddy_manager.remove_campaign_config(campaign.uid)
                        else:
                            caddy_manager.deploy_campaign_config(campaign)  # rebuild without this campaign
                        campaign.caddy_config_id = None
                    except Exception as e:
                        logger.warning(f"Failed to remove Caddy config for campaign {campaign.uid}: {e}")

            # Redeploy Caddy config for already-active campaigns when
            # config-affecting fields change (gate token, UA filter, etc.)
            if new_status == 'active' and needs_caddy and old_status == 'active':
                from shared.caddy import caddy_manager, CaddyAPIError
                db.session.flush()
                try:
                    caddy_manager.deploy_campaign_config(campaign)
                    campaign.caddy_config_id = f"campaign_{campaign.uid or campaign.custom_domain}"
                except CaddyAPIError as e:
                    logger.warning(f"Caddy redeploy error for campaign {campaign.uid}: {e}")

            # Run phishing detection on every update for inbound campaigns,
            # regardless of whether HTML changed — keeps the score fresh and
            # backfills campaigns that landed without one.
            if current_type == 'inbound':
                self._run_phishing_detection(campaign)
            
            db.session.commit()
            
            logger.info(f"Campaign updated: {campaign.name} (ID: {campaign.id}, Type: {current_type})")
            return campaign
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to update campaign {campaign.id}: {e}")
            raise
    
    def delete_campaign(self, campaign: Campaign):
        """Delete a campaign (soft delete by setting status)"""
        try:
            campaign.status = 'deleted'
            campaign.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            logger.info(f"Campaign deleted: {campaign.name} (ID: {campaign.id})")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to delete campaign {campaign.id}: {e}")
            raise
    
    def start_campaign(self, campaign: Campaign):
        """Start a campaign"""
        try:
            if campaign.status != 'draft':
                raise ValueError(f"Campaign must be in draft status to start (current: {campaign.status})")
            
            campaign.status = 'active'
            campaign.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            logger.info(f"Campaign started: {campaign.name} (ID: {campaign.id})")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to start campaign {campaign.id}: {e}")
            raise
    
    def stop_campaign(self, campaign: Campaign):
        """Stop a campaign"""
        try:
            if campaign.status != 'active':
                raise ValueError(f"Campaign must be active to stop (current: {campaign.status})")
            
            campaign.status = 'completed'
            campaign.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            logger.info(f"Campaign stopped: {campaign.name} (ID: {campaign.id})")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to stop campaign {campaign.id}: {e}")
            raise
    
    def clone_campaign(self, campaign: Campaign, user: User) -> Campaign:
        """Clone an existing campaign"""
        try:
            cloned_campaign = Campaign(
                name=f"{campaign.name} (Copy)",
                description=campaign.description,
                get_workflow_id=campaign.get_workflow_id,
                post_workflow_id=campaign.post_workflow_id,
                template_id=campaign.template_id,
                template_html=campaign.template_html,
                config=campaign.config.copy() if campaign.config else {},
                variables=campaign.variables.copy() if campaign.variables else {},
                created_by_id=user.id,
                uid=self._generate_unique_uid()
            )
            
            db.session.add(cloned_campaign)
            db.session.commit()
            
            logger.info(f"Campaign cloned: {campaign.name} -> {cloned_campaign.name} (ID: {cloned_campaign.id})")
            return cloned_campaign
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to clone campaign {campaign.id}: {e}")
            raise
    
    def add_tracked_user(self, campaign: Campaign, request: TrackedUserRequest) -> TrackedUser:
        """Add a tracked user to a campaign"""
        try:
            # Check if user already exists for this campaign
            existing_user = TrackedUser.query.filter(
                TrackedUser.campaign_id == campaign.id,
                TrackedUser.email == request.email
            ).first()
            
            if existing_user:
                raise ValueError(f"User {request.email} is already tracked for this campaign")
            
            tracked_user = TrackedUser(
                campaign_id=campaign.id,
                email=request.email,
                first_name=request.first_name,
                last_name=request.last_name,
                department=request.department,
                tracking_id=str(uuid.uuid4())
            )
            
            db.session.add(tracked_user)
            db.session.commit()
            
            logger.info(f"Tracked user added: {request.email} to campaign {campaign.name}")
            return tracked_user
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to add tracked user to campaign {campaign.id}: {e}")
            raise
    
    def _run_phishing_detection(self, campaign: Campaign):
        """Run phishing detection on campaign template HTML"""
        try:
            if not campaign.template_html:
                return
            
            detection_result = detect_phishing(campaign.template_html)
            
            campaign.phishing_score = detection_result.get('confidence', 0.0)
            campaign.phishing_is_phishing = detection_result.get('is_phishing', False)
            campaign.phishing_detected_at = datetime.utcnow()
            
            if detection_result.get('error'):
                logger.warning(f"Phishing detection error for campaign {campaign.id}: {detection_result['error']}")
            else:
                logger.info(f"Phishing detection for campaign {campaign.id}: is_phishing={campaign.phishing_is_phishing}, confidence={campaign.phishing_score:.3f}")
                
        except Exception as e:
            logger.error(f"Failed to run phishing detection for campaign {campaign.id}: {e}", exc_info=True)
            # Don't fail the operation if detection fails
    
    def _generate_unique_uid(self) -> str:
        """Generate a unique campaign UID"""
        while True:
            uid = str(uuid.uuid4())[:8]
            if not Campaign.query.filter_by(uid=uid).first():
                return uid

class TemplateService:
    """Service for template operations"""
    
    def __init__(self):
        # Create sandboxed Jinja2 environment.
        # ChainableUndefined: missing-variable access ({{ target.name }} when
        # target isn't supplied) renders to empty string instead of raising
        # UndefinedError — appropriate for the preview surface, where operators
        # are iterating on templates and should see best-effort output.
        self.jinja_env = SandboxedEnvironment(autoescape=True, undefined=ChainableUndefined)

        # Add safe functions to globals
        self.jinja_env.globals.update({
            'range': range,
            'len': len,
            'str': str,
            'int': int,
            'float': float,
            'bool': bool
        })
    
    def create_template(self, request: TemplateCreateRequest, user: User) -> Template:
        """Create a new template"""
        try:
            template = Template(
                name=request.name,
                description=request.description,
                category=request.category,
                template_type=request.template_type or 'main',
                template_html=request.template_html,
                preview_image=request.preview_image,
                is_public=request.is_public,
                variables=request.variables or [],
                created_by_id=user.id
            )
            
            db.session.add(template)
            db.session.flush()  # Get template ID
            
            # Run phishing detection
            self._run_phishing_detection_template(template)
            
            db.session.commit()
            
            logger.info(f"Template created: {template.name} (ID: {template.id}) by user {user.username}")
            return template
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to create template: {e}")
            raise
    
    def update_template(self, template: Template, request: TemplateUpdateRequest) -> Template:
        """Update an existing template"""
        try:
            if request.name is not None:
                template.name = request.name
            if request.description is not None:
                template.description = request.description
            if request.category is not None:
                template.category = request.category
            if request.template_type is not None:
                template.template_type = request.template_type
            html_changed = False
            if request.template_html is not None:
                if template.template_html != request.template_html:
                    html_changed = True
                    # Reset override when HTML changes
                    template.phishing_override = False
                    template.phishing_approved_by_id = None
                    template.phishing_approved_at = None
                template.template_html = request.template_html
            if request.preview_image is not None:
                template.preview_image = request.preview_image
            if request.is_public is not None:
                template.is_public = request.is_public
            if request.variables is not None:
                template.variables = request.variables
            
            template.updated_at = datetime.utcnow()

            # Run phishing detection on every update so metadata-only edits also
            # (re)score the template — keeps the badge accurate after rename /
            # category / variable changes, and backfills templates that landed
            # in the DB without a score (clones, imports, pre-feature rows).
            self._run_phishing_detection_template(template)
            
            db.session.commit()
            
            logger.info(f"Template updated: {template.name} (ID: {template.id})")
            return template
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to update template {template.id}: {e}")
            raise
    
    def delete_template(self, template: Template):
        """Delete a template"""
        try:
            db.session.delete(template)
            db.session.commit()
            
            logger.info(f"Template deleted: {template.name} (ID: {template.id})")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to delete template {template.id}: {e}")
            raise
    
    def _run_phishing_detection_template(self, template: Template):
        """Run phishing detection on template HTML"""
        try:
            if not template.template_html:
                return
            
            detection_result = detect_phishing(template.template_html)
            
            template.phishing_score = detection_result.get('confidence', 0.0)
            template.phishing_is_phishing = detection_result.get('is_phishing', False)
            template.phishing_detected_at = datetime.utcnow()
            
            if detection_result.get('error'):
                logger.warning(f"Phishing detection error for template {template.id}: {detection_result['error']}")
            else:
                logger.info(f"Phishing detection for template {template.id}: is_phishing={template.phishing_is_phishing}, confidence={template.phishing_score:.3f}")
                
        except Exception as e:
            logger.error(f"Failed to run phishing detection for template {template.id}: {e}", exc_info=True)
            # Don't fail the operation if detection fails
    
    def clone_template(self, template: Template, user: User) -> Template:
        """Clone an existing template and its assets."""
        try:
            cloned_template = Template(
                name=f"{template.name} (Copy)",
                description=template.description,
                category=template.category,
                template_type=template.template_type or 'main',
                template_html=template.template_html,
                preview_image=None,  # Don't copy preview image; clone can regenerate via preview
                is_public=False,  # Cloned templates start as private
                variables=template.variables.copy() if template.variables else [],
                created_by_id=user.id
            )
            
            db.session.add(cloned_template)
            db.session.flush()  # Get cloned_template.id before detection logs reference it

            # Score the clone separately; the source's classification doesn't carry over.
            self._run_phishing_detection_template(cloned_template)

            db.session.commit()

            # Copy asset files from source template to cloned template
            try:
                from shared.caddy import caddy_manager
                source_dir = caddy_manager.get_template_assets_dir(template.id)
                dest_dir = caddy_manager.get_template_assets_dir(cloned_template.id)
                assets_copied = 0
                if source_dir.exists():
                    for path in source_dir.iterdir():
                        if path.is_file():
                            shutil.copy2(path, dest_dir / path.name)
                            assets_copied += 1
                    if assets_copied:
                        logger.info(f"Cloned {assets_copied} assets for template {cloned_template.id}")
            except Exception as asset_err:
                logger.warning(f"Could not copy template assets when cloning: {asset_err}")
                # Don't fail the clone if asset copy fails

            logger.info(f"Template cloned: {template.name} -> {cloned_template.name} (ID: {cloned_template.id})")
            return cloned_template
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to clone template {template.id}: {e}")
            raise
    
    def validate_jinja_template(self, template_html: str) -> List[str]:
        """Validate Jinja2 template syntax"""
        try:
            # Parse the template to check for syntax errors
            self.jinja_env.parse(template_html)
            
            # Extract template variables
            variables = meta.find_undeclared_variables(self.jinja_env.parse(template_html))
            
            # Check for potentially dangerous variables/functions
            dangerous_patterns = [
                'import', '__', 'eval', 'exec', 'open', 'file',
                'input', 'raw_input', 'compile', 'reload'
            ]
            
            warnings = []
            for var in variables:
                if any(pattern in var.lower() for pattern in dangerous_patterns):
                    warnings.append(f"Potentially dangerous variable: {var}")
            
            return warnings
            
        except TemplateSyntaxError as e:
            raise ValueError(f"Jinja2 template syntax error: {e}")
        except Exception as e:
            raise ValueError(f"Template validation error: {e}")
    
    def validate_javascript(self, js_code: str) -> List[str]:
        """Validate JavaScript code for custom form validation"""
        try:
            # Basic JavaScript syntax validation
            # Note: This is a simple check - for production, consider using a proper JS parser
            warnings = []
            
            # Check for dangerous patterns
            dangerous_patterns = [
                'eval(', 'setTimeout(', 'setInterval(', 'Function(',
                'document.write', 'innerHTML', 'outerHTML',
                'location.', 'window.location', 'document.location',
                'fetch(', 'XMLHttpRequest', 'import(', 'require('
            ]
            
            for pattern in dangerous_patterns:
                if pattern in js_code:
                    warnings.append(f"Potentially dangerous pattern: {pattern}")
            
            # Basic syntax check
            if js_code.count('(') != js_code.count(')'):
                warnings.append("Mismatched parentheses")
            if js_code.count('{') != js_code.count('}'):
                warnings.append("Mismatched braces")
            if js_code.count('[') != js_code.count(']'):
                warnings.append("Mismatched brackets")
            
            return warnings
            
        except Exception as e:
            raise ValueError(f"JavaScript validation error: {e}")
    
    def render_template_preview(self, template: Template, preview_data: Dict[str, Any]) -> str:
        """Render template with preview data"""
        try:
            # Create template with safe defaults for missing variables
            jinja_template = self.jinja_env.from_string(template.template_html)
            
            # Add default values for common template variables
            default_data = {
                'company_name': 'Example Corp',
                'user_email': 'user@example.com',
                'user_name': 'John Doe',
                'login_url': 'https://example.com/login',
                'support_email': 'support@example.com',
                'current_date': datetime.now().strftime('%Y-%m-%d'),
                'campaign_url': 'https://example.com/campaign',
                # Tracked-user shape — templates commonly reference {{ target.name }},
                # {{ target.email }}, etc. for personalized phishing copy.
                'target': {
                    'name': 'Jane Smith',
                    'first_name': 'Jane',
                    'last_name': 'Smith',
                    'email': 'jane.smith@example.com',
                    'department': 'Finance',
                    'tracking_id': '00000000-0000-0000-0000-000000000000',
                },
            }
            
            # Merge with provided preview data
            render_data = {**default_data, **preview_data}
            
            # Render template
            rendered_html = jinja_template.render(**render_data)
            
            return rendered_html
            
        except Exception as e:
            raise ValueError(f"Template rendering error: {e}")
    
    def get_template_variables(self, template_html: str) -> List[str]:
        """Extract all variables from a Jinja2 template"""
        try:
            ast = self.jinja_env.parse(template_html)
            variables = meta.find_undeclared_variables(ast)
            return sorted(list(variables))
            
        except Exception as e:
            logger.error(f"Failed to extract template variables: {e}")
            return []

    def save_template_preview_image(self, template_id: int, image_data: bytes) -> Template:
        """Save preview image for a template; creates storage/template_previews/ and overwrites existing file."""
        template = Template.query.get(template_id)
        if not template:
            raise ValueError("Template not found")
        # Optional: validate PNG magic bytes
        png_header = b'\x89PNG\r\n\x1a\n'
        if not image_data.startswith(png_header):
            raise ValueError("Invalid image: expected PNG format")
        storage_base = Path("storage")
        previews_dir = storage_base / "template_previews"
        previews_dir.mkdir(parents=True, exist_ok=True)
        file_path = previews_dir / f"{template_id}.png"
        file_path.write_bytes(image_data)
        relative_path = f"template_previews/{template_id}.png"
        template.preview_image = relative_path
        template.updated_at = datetime.utcnow()
        db.session.commit()
        logger.info(f"Preview image saved for template {template_id}")
        return template

    def get_categories(self) -> List[Dict[str, Any]]:
        """Get all template categories with counts"""
        try:
            categories = db.session.query(
                Template.category,
                db.func.count(Template.id).label('count')
            ).filter(
                Template.category.isnot(None)
            ).group_by(Template.category).all()
            
            return [
                {
                    'name': category,
                    'count': count,
                    'display_name': category.title() if category else 'Uncategorized'
                }
                for category, count in categories
            ]
            
        except Exception as e:
            logger.error(f"Failed to get template categories: {e}")
            return []

class StatisticsService:
    """Service for statistics and reporting"""
    
    def get_campaign_stats(self, campaign_id: int) -> Dict[str, Any]:
        """Get statistics for a specific campaign"""
        try:
            campaign = Campaign.query.get_or_404(campaign_id)
            
            # Get event counts by type
            event_counts = db.session.query(
                Event.event_type,
                db.func.count(Event.id).label('count')
            ).filter(
                Event.campaign_id == campaign_id
            ).group_by(Event.event_type).all()
            
            event_stats = {event_type: count for event_type, count in event_counts}
            
            # Get tracked user stats
            tracked_users_count = TrackedUser.query.filter(
                TrackedUser.campaign_id == campaign_id
            ).count()
            
            # Calculate success rates (use actual event_type values: page_view, credentials, form_submit)
            page_visits = event_stats.get('page_view', 0)
            credentials_captured = event_stats.get('credentials', 0)
            
            success_rate = (credentials_captured / page_visits * 100) if page_visits > 0 else 0
            
            return {
                'campaign_id': campaign_id,
                'campaign_name': campaign.name,
                'campaign_status': campaign.status,
                'event_stats': event_stats,
                'tracked_users_count': tracked_users_count,
                'success_rate': round(success_rate, 2),
                'total_events': sum(event_stats.values()),
                'created_at': campaign.created_at.isoformat(),
                'updated_at': campaign.updated_at.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to get campaign stats for {campaign_id}: {e}")
            raise
    
    def get_dashboard_overview(self) -> Dict[str, Any]:
        """Get dashboard overview statistics"""
        try:
            # Campaign statistics (exclude soft-deleted)
            total_campaigns = Campaign.query.filter(Campaign.status != 'deleted').count()
            active_campaigns = Campaign.query.filter(Campaign.status == 'active').count()
            draft_campaigns = Campaign.query.filter(Campaign.status == 'draft').count()
            completed_campaigns = Campaign.query.filter(Campaign.status == 'completed').count()
            
            # Template statistics
            total_templates = Template.query.count()
            public_templates = Template.query.filter(Template.is_public == True).count()
            
            # Event statistics (last 30 days)
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            recent_events = Event.query.filter(Event.created_at >= thirty_days_ago).count()
            
            # Credentials captured (last 30 days)
            recent_credentials = Event.query.filter(
                Event.event_type == 'credentials',
                Event.created_at >= thirty_days_ago
            ).count()
            
            return {
                'total_campaigns': total_campaigns,
                'active_campaigns': active_campaigns,
                'draft_campaigns': draft_campaigns,
                'completed_campaigns': completed_campaigns,
                'total_templates': total_templates,
                'public_templates': public_templates,
                'recent_events': recent_events,
                'recent_credentials': recent_credentials,
                'generated_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to get dashboard overview: {e}")
            raise

    def get_campaign_series(
        self,
        campaign_id: int,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        group_by: str = 'day',
    ) -> Dict[str, Any]:
        """Get time-series data for campaign reporting charts. Returns labels (dates) and series (counts per day)."""
        from sqlalchemy import func

        campaign = Campaign.query.get_or_404(campaign_id)
        now = datetime.utcnow()
        to_d = to_date if to_date else now.date()
        from_d = from_date if from_date else (to_d - timedelta(days=30))
        if hasattr(from_d, 'date'):
            from_d = from_d.date()
        if hasattr(to_d, 'date'):
            to_d = to_d.date()

        labels = []
        d = from_d
        while d <= to_d:
            labels.append(d.isoformat())
            d += timedelta(days=1)

        result = {'labels': labels, 'campaign_type': campaign.campaign_type}
        from_naive = datetime.combine(from_d, datetime.min.time())
        to_naive_end = datetime.combine(to_d + timedelta(days=1), datetime.min.time())

        if campaign.campaign_type == 'inbound':
            q = (
                db.session.query(
                    func.date(Event.created_at).label('day'),
                    Event.event_type,
                    db.func.count(Event.id).label('count'),
                )
                .filter(
                    Event.campaign_id == campaign_id,
                    Event.created_at >= from_naive,
                    Event.created_at < to_naive_end,
                    Event.event_type.in_(['page_view', 'credentials']),
                )
                .group_by(func.date(Event.created_at), Event.event_type)
                .all()
            )
            by_day_type = {(str(row.day), row.event_type): row.count for row in q}
            result['inbound'] = {
                'page_views': [by_day_type.get((day, 'page_view'), 0) for day in labels],
                'credentials': [by_day_type.get((day, 'credentials'), 0) for day in labels],
            }
        else:
            # Outbound: EmailJob (via SendingWorkflow) and CallJob by date and status
            email_rows = (
                db.session.query(
                    func.coalesce(
                        func.date(EmailJob.sent_at),
                        func.date(EmailJob.created_at),
                    ).label('day'),
                    EmailJob.status,
                    db.func.count(EmailJob.id).label('count'),
                )
                .join(SendingWorkflow, EmailJob.sending_workflow_id == SendingWorkflow.id)
                .filter(
                    SendingWorkflow.campaign_id == campaign_id,
                    EmailJob.created_at >= from_naive,
                    EmailJob.created_at < to_naive_end,
                )
                .group_by(func.coalesce(func.date(EmailJob.sent_at), func.date(EmailJob.created_at)), EmailJob.status)
                .all()
            )
            call_rows = (
                db.session.query(
                    func.coalesce(
                        func.date(CallJob.completed_at),
                        func.date(CallJob.created_at),
                    ).label('day'),
                    CallJob.status,
                    db.func.count(CallJob.id).label('count'),
                )
                .filter(
                    CallJob.campaign_id == campaign_id,
                    CallJob.created_at >= from_naive,
                    CallJob.created_at < to_naive_end,
                )
                .group_by(
                    func.coalesce(func.date(CallJob.completed_at), func.date(CallJob.created_at)),
                    CallJob.status,
                )
                .all()
            )
            by_day_email = {}
            for row in email_rows:
                day_str = str(row.day) if row.day else None
                if not day_str:
                    continue
                key = (day_str, 'sent' if row.status == 'sent' else 'failed')
                by_day_email[key] = by_day_email.get(key, 0) + row.count
            by_day_call = {}
            for row in call_rows:
                day_str = str(row.day) if row.day else None
                if not day_str:
                    continue
                key = (day_str, 'completed' if row.status == 'completed' else 'failed')
                by_day_call[key] = by_day_call.get(key, 0) + row.count
            result['outbound'] = {
                'email_sent': [by_day_email.get((day, 'sent'), 0) for day in labels],
                'email_failed': [by_day_email.get((day, 'failed'), 0) for day in labels],
                'call_completed': [by_day_call.get((day, 'completed'), 0) for day in labels],
                'call_failed': [by_day_call.get((day, 'failed'), 0) for day in labels],
            }
        return result
