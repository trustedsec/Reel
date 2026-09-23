"""
API request and response models using Pydantic for validation
"""
from pydantic import BaseModel, validator, Field, model_validator
from typing import Optional, Dict, Any, List
from datetime import datetime

class CampaignCreateRequest(BaseModel):
    """Request model for creating a campaign"""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    campaign_type: str = Field('inbound', description="Campaign type: 'inbound' or 'outbound'")
    get_workflow_id: Optional[int] = Field(None, description="Workflow ID for GET requests")
    post_workflow_id: Optional[int] = Field(None, description="Workflow ID for POST requests")
    template_id: Optional[int] = Field(None, description="ID of template to use")
    template_html: Optional[str] = Field(None, description="Custom HTML if not using template")
    config: Dict[str, Any] = Field(default_factory=dict)
    variables: Dict[str, Any] = Field(default_factory=dict, description="Campaign variables for workflows")
    ssl_mode: Optional[str] = Field(None, description="SSL mode: automatic, custom, self_signed, or disabled")
    custom_domain: Optional[str] = Field(None, description="Custom domain for the campaign")
    is_mms_enabled: Optional[bool] = Field(None, description="Outbound: route custom_domain via Caddy for MMS card delivery")
    ua_filter_enabled: Optional[bool] = Field(None, description="Enable User-Agent filtering")
    ua_deny_list: Optional[List[str]] = Field(None, description="User-Agent strings to block")
    ua_blocked_template_id: Optional[int] = Field(None, description="Template to show when blocked")
    captcha_enabled: Optional[bool] = Field(None, description="Require CAPTCHA before landing page")
    captcha_template_id: Optional[int] = Field(None, description="CAPTCHA template ID")
    gate_enabled: Optional[bool] = Field(None)
    gate_token: Optional[str] = Field(None, max_length=50)
    gate_param_name: Optional[str] = Field(None, max_length=50)
    gate_mode: Optional[str] = Field(None)
    gate_redirect_url: Optional[str] = Field(None, max_length=500)
    gate_template_id: Optional[int] = None
    allowed_proxy_groups: Optional[List[str]] = Field(None, description="Proxy path groups to enable: tracking, credential_proxy, media")

    @validator('name')
    def validate_name(cls, v):
        if not v.strip():
            raise ValueError('Campaign name cannot be empty')
        return v.strip()

    @validator('campaign_type')
    def validate_campaign_type(cls, v):
        if v not in ('inbound', 'outbound'):
            raise ValueError("campaign_type must be 'inbound' or 'outbound'")
        return v
    
    @model_validator(mode='after')
    def validate_campaign(self):
        # Type-specific validation
        if self.campaign_type == 'inbound':
            # Inbound campaigns require at least one workflow
            get_wf = self.get_workflow_id
            post_wf = self.post_workflow_id
            
            if not get_wf and not post_wf:
                raise ValueError('Inbound campaigns require at least one workflow (get_workflow_id or post_workflow_id)')
            
            # Inbound campaigns may have no template when using workflow-defined templates (Render Template node)
            # Both template_id and template_html can be None/empty for inbound in that case
        # Outbound campaigns don't require workflows or templates (but can have templates for email content)
        
        return self

class CampaignUpdateRequest(BaseModel):
    """Request model for updating a campaign"""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    campaign_type: Optional[str] = Field(None, description="Campaign type: 'inbound' or 'outbound'")
    uid: Optional[str] = Field(None, description="Campaign UID (unique identifier)")
    get_workflow_id: Optional[int] = Field(None, description="Workflow ID for GET requests")
    post_workflow_id: Optional[int] = Field(None, description="Workflow ID for POST requests")
    template_id: Optional[int] = None
    template_html: Optional[str] = None
    template_source: Optional[str] = Field(None, description="'existing', 'custom', or 'workflow_defined'; when 'workflow_defined', template_id and template_html are cleared for inbound")
    config: Optional[Dict[str, Any]] = None
    variables: Optional[Dict[str, Any]] = Field(None, description="Campaign variables for workflows")
    status: Optional[str] = None
    ssl_mode: Optional[str] = Field(None, description="SSL mode: automatic, custom, self_signed, or disabled")
    custom_domain: Optional[str] = Field(None, description="Custom domain for the campaign")
    is_mms_enabled: Optional[bool] = Field(None, description="Outbound: route custom_domain via Caddy for MMS card delivery")
    captcha_enabled: Optional[bool] = None
    captcha_template_id: Optional[int] = None
    ua_filter_enabled: Optional[bool] = None
    ua_deny_list: Optional[List[str]] = None
    ua_blocked_template_id: Optional[int] = None
    gate_enabled: Optional[bool] = Field(None)
    gate_token: Optional[str] = Field(None, max_length=50)
    gate_param_name: Optional[str] = Field(None, max_length=50)
    gate_mode: Optional[str] = Field(None)
    gate_redirect_url: Optional[str] = Field(None, max_length=500)
    gate_template_id: Optional[int] = None
    allowed_proxy_groups: Optional[List[str]] = Field(None, description="Proxy path groups to enable: tracking, credential_proxy, media")

    @validator('name')
    def validate_name(cls, v):
        if v is not None and not v.strip():
            raise ValueError('Campaign name cannot be empty')
        return v.strip() if v else v

    @validator('campaign_type')
    def validate_campaign_type(cls, v):
        if v is not None and v not in ('inbound', 'outbound'):
            raise ValueError("campaign_type must be 'inbound' or 'outbound'")
        return v
    
    @validator('status')
    def validate_status(cls, v):
        if v is not None:
            allowed_statuses = ['draft', 'active', 'paused', 'completed']
            if v not in allowed_statuses:
                raise ValueError(f'Status must be one of: {", ".join(allowed_statuses)}')
        return v

class TrackedUserRequest(BaseModel):
    """Request model for adding tracked users"""
    email: str = Field(..., pattern=r'^[^@]+@[^@]+\.[^@]+$')
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    
    @validator('email')
    def validate_email(cls, v):
        return v.lower().strip()

class TemplateCreateRequest(BaseModel):
    """Request model for creating a template"""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    category: Optional[str] = Field(None, max_length=50)
    template_type: Optional[str] = Field('main', description="Template type: main, captcha, error")
    template_html: str = Field(..., min_length=1, description="Jinja2 template HTML")
    preview_image: Optional[str] = Field(None, description="Preview image URL or base64")
    is_public: bool = Field(True, description="Whether template is public")
    variables: Optional[List[Dict[str, str]]] = Field(
        default_factory=list,
        description="Template variables with name, type, description, default"
    )

    @validator('template_type')
    def validate_template_type(cls, v):
        if v is None:
            return 'main'
        allowed = ['main', 'captcha', 'error']
        if v not in allowed:
            raise ValueError(f'template_type must be one of: {", ".join(allowed)}')
        return v
    
    @validator('name')
    def validate_name(cls, v):
        if not v.strip():
            raise ValueError('Template name cannot be empty')
        return v.strip()
    
    @validator('category')
    def validate_category(cls, v):
        if v:
            # Predefined categories for consistency (must match frontend dropdown)
            allowed_categories = [
                'office365', 'gmail', 'outlook', 'login',
                'microsoft', 'google', 'aws', 'azure',
                'banking', 'social', 'ecommerce', 'portal',
                'generic', 'custom', 'other'
            ]
            if v.lower() not in allowed_categories:
                raise ValueError(f'Category must be one of: {", ".join(allowed_categories)}')
            return v.lower()
        return v

    @validator('variables')
    def validate_variables(cls, v):
        if v:
            required_keys = {'name', 'type', 'description'}
            for var in v:
                if not isinstance(var, dict):
                    raise ValueError('Variables must be objects')
                if not required_keys.issubset(var.keys()):
                    raise ValueError(f'Variable must have keys: {", ".join(required_keys)}')
                # Validate variable types
                allowed_types = ['text', 'email', 'url', 'number', 'boolean', 'select']
                if var['type'] not in allowed_types:
                    raise ValueError(f'Variable type must be one of: {", ".join(allowed_types)}')
        return v

class TemplateUpdateRequest(BaseModel):
    """Request model for updating a template"""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    category: Optional[str] = Field(None, max_length=50)
    template_type: Optional[str] = Field(None, description="Template type: main, captcha, error")
    template_html: Optional[str] = Field(None, description="Jinja2 template HTML")
    preview_image: Optional[str] = Field(None, description="Preview image URL or base64")
    is_public: Optional[bool] = Field(None, description="Whether template is public")
    variables: Optional[List[Dict[str, str]]] = Field(None, description="Template variables")

    @validator('template_type')
    def validate_template_type(cls, v):
        if v is None:
            return v
        allowed = ['main', 'captcha', 'error']
        if v not in allowed:
            raise ValueError(f'template_type must be one of: {", ".join(allowed)}')
        return v
    
    @validator('name')
    def validate_name(cls, v):
        if v is not None and not v.strip():
            raise ValueError('Template name cannot be empty')
        return v.strip() if v else v
    
    @validator('category')
    def validate_category(cls, v):
        if v:
            allowed_categories = [
                'office365', 'gmail', 'outlook', 'login',
                'microsoft', 'google', 'aws', 'azure',
                'banking', 'social', 'ecommerce', 'portal',
                'generic', 'custom', 'other'
            ]
            if v.lower() not in allowed_categories:
                raise ValueError(f'Category must be one of: {", ".join(allowed_categories)}')
            return v.lower()
        return v

    @validator('variables')
    def validate_variables(cls, v):
        if v is not None:
            required_keys = {'name', 'type', 'description'}
            for var in v:
                if not isinstance(var, dict):
                    raise ValueError('Variables must be objects')
                if not required_keys.issubset(var.keys()):
                    raise ValueError(f'Variable must have keys: {", ".join(required_keys)}')
                allowed_types = ['text', 'email', 'url', 'number', 'boolean', 'select']
                if var['type'] not in allowed_types:
                    raise ValueError(f'Variable type must be one of: {", ".join(allowed_types)}')
        return v
