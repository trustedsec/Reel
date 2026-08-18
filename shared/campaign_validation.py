"""
Campaign validation for activation.
Pure validation logic - no Flask/request dependencies.
"""
from shared.database import Template, Workflow


def validate_campaign_for_activation(campaign) -> list:
    """
    Validate campaign before activation.

    Args:
        campaign: Campaign model instance

    Returns:
        List of error strings. Empty list means validation passed.
    """
    errors = []

    # Type-specific validation
    if campaign.campaign_type == 'inbound':
        # Inbound campaigns may have no template when using workflow-defined templates (both can be empty)
        # Require at least one workflow
        if not campaign.get_workflow_id and not campaign.post_workflow_id:
            errors.append("Inbound campaigns require at least one workflow (GET or POST)")

        # Inbound campaigns require UID
        if not campaign.uid:
            errors.append("Inbound campaigns require a UID")

        # CAPTCHA: if enabled, require template and Turnstile keys
        if getattr(campaign, 'captcha_enabled', False):
            if not campaign.captcha_template_id:
                errors.append("CAPTCHA is enabled but no CAPTCHA template is selected")
            config = campaign.config or {}
            if not config.get('captcha_site_key') or not config.get('captcha_secret_key'):
                errors.append("CAPTCHA is enabled but Turnstile site key or secret key is missing")

        # Gate token: if enabled, require template or redirect URL based on mode
        if getattr(campaign, 'gate_enabled', False):
            gate_mode = getattr(campaign, 'gate_mode', 'template') or 'template'
            if gate_mode == 'template' and not getattr(campaign, 'gate_template_id', None):
                errors.append("Gate token is enabled but no decoy template is selected")
            if gate_mode == 'redirect' and not getattr(campaign, 'gate_redirect_url', None):
                errors.append("Gate token is enabled but no redirect URL is set")
    elif campaign.campaign_type == 'outbound':
        if not campaign.post_workflow_id:
            errors.append("Outbound campaigns require an outbound workflow.")

    # Check if template exists and is valid
    if campaign.template_id:
        template = Template.query.get(campaign.template_id)
        if not template:
            errors.append("Selected template no longer exists")
        elif not template.template_html:
            errors.append("Selected template has no HTML content")
        else:
            # Validate Jinja2 template syntax
            try:
                from jinja2 import Template as Jinja2Template
                Jinja2Template(template.template_html)
            except Exception as e:
                errors.append(f"Template syntax error: {str(e)}")

    # Validate custom HTML if provided
    if campaign.template_html:
        try:
            from jinja2 import Template as Jinja2Template
            Jinja2Template(campaign.template_html)
        except Exception as e:
            errors.append(f"Custom HTML syntax error: {str(e)}")

    # Ensure config is at least an empty dict (None means it was never initialized)
    if campaign.config is None:
        errors.append("Campaign configuration is missing")

    # Validate workflows exist
    if campaign.get_workflow_id:
        get_workflow = Workflow.query.get(campaign.get_workflow_id)
        if not get_workflow:
            errors.append(f"Workflow {campaign.get_workflow_id} not found")
        elif campaign.campaign_type == 'inbound' and get_workflow.http_method not in ('GET', 'BOTH'):
            # Only validate HTTP method for inbound campaigns
            errors.append(f"GET workflow {campaign.get_workflow_id} does not support GET requests")

    if campaign.post_workflow_id:
        post_workflow = Workflow.query.get(campaign.post_workflow_id)
        if not post_workflow:
            errors.append(f"Workflow {campaign.post_workflow_id} not found")
        elif campaign.campaign_type == 'inbound' and post_workflow.http_method not in ('POST', 'BOTH'):
            # Only validate HTTP method for inbound campaigns
            errors.append(f"POST workflow {campaign.post_workflow_id} does not support POST requests")

    if campaign.campaign_type == 'inbound' and not campaign.get_workflow_id and not campaign.post_workflow_id:
        errors.append("At least one workflow (GET or POST) must be assigned")

    # Check if UID is unique and valid (inbound only)
    if campaign.campaign_type == 'inbound':
        if not campaign.uid:
            errors.append("Campaign UID is missing")
        elif len(campaign.uid) < 3:
            errors.append("Campaign UID must be at least 3 characters")

    return errors
