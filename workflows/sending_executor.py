"""
Sending workflow executor - executes email sending workflows
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
from shared.database import db, SendingWorkflow, EmailJob, Campaign, Workflow
from plugins import get_plugin
from workflows.engine import execute_workflow as run_workflow_engine
from jinja2 import Template
import logging
import time

logger = logging.getLogger(__name__)

class SendingWorkflowExecutor:
    """Executor for sending workflows"""
    
    def __init__(self):
        pass
    
    def execute_sending_workflow(
        self,
        sending_workflow_id: int,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Execute a sending workflow
        
        Args:
            sending_workflow_id: ID of sending workflow to execute
            dry_run: If True, don't actually send emails, just validate
            
        Returns:
            Execution result with statistics
        """
        sending_workflow = SendingWorkflow.query.get(sending_workflow_id)
        if not sending_workflow:
            raise ValueError(f"Sending workflow {sending_workflow_id} not found")
        
        # Check if already running
        if sending_workflow.status == 'running':
            raise ValueError(f"Sending workflow {sending_workflow_id} is already running")
        
        workflow_id = getattr(sending_workflow, 'workflow_id', None)
        if not workflow_id:
            raise ValueError(
                "This sending workflow has no workflow graph. Create a new sending workflow from the Workflow builder."
            )
        
        try:
            # Update status
            sending_workflow.status = 'running'
            sending_workflow.started_at = datetime.utcnow()
            db.session.commit()
            
            return self._execute_sending_workflow_via_engine(sending_workflow, workflow_id, dry_run)
            
        except Exception as e:
            logger.error(f"Sending workflow {sending_workflow_id} execution failed: {e}", exc_info=True)
            sending_workflow.status = 'failed'
            sending_workflow.completed_at = datetime.utcnow()
            db.session.commit()
            raise
    
    def _execute_sending_workflow_via_engine(
        self,
        sending_workflow: SendingWorkflow,
        workflow_id: int,
        dry_run: bool,
    ) -> Dict[str, Any]:
        """Execute sending workflow via node-based workflow engine (single run with Loop in graph)."""
        workflow = Workflow.query.get(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        if not workflow.is_active:
            raise ValueError(f"Workflow {workflow_id} is not active")
        campaign = sending_workflow.campaign
        if not campaign:
            raise ValueError("Set campaign before running this sending workflow")
        campaign_dict = campaign.to_dict(include_template=True)
        config = campaign_dict.get('config', {}) or {}
        initial_context = {
            'campaign': campaign_dict,
            'campaign_id': campaign.id,
            'variables': getattr(campaign, 'variables', None) or {},
            'sending_workflow_id': sending_workflow.id,
            **config,
        }
        # Provide default 'url' for url_obfuscator / plugins (campaign landing URL)
        if 'url' not in initial_context:
            landing = config.get('url') or config.get('landing_url') or config.get('phishing_url')
            if landing:
                initial_context['url'] = landing
            else:
                try:
                    from shared.config import Config
                    cfg = Config()
                    host = getattr(cfg, 'PHISHING_HOST', '127.0.0.1') or '127.0.0.1'
                    if host == '0.0.0.0':
                        host = '127.0.0.1'
                    port = getattr(cfg, 'PHISHING_PORT', 1234) or 1234
                    uid = getattr(campaign, 'uid', '') or ''
                    initial_context['url'] = f"http://{host}:{port}/{uid}" if uid else f"http://{host}:{port}"
                except Exception:
                    initial_context['url'] = ''
        if dry_run:
            sending_workflow.status = 'completed'
            sending_workflow.completed_at = datetime.utcnow()
            db.session.commit()
            return {'status': 'completed', 'total': 0, 'sent': 0, 'failed': 0, 'pending': 0}
        try:
            result_context = run_workflow_engine(
                workflow_id=workflow_id,
                context=initial_context,
                campaign_id=campaign.id,
                hook_name='sending',
            )
        except Exception as e:
            logger.exception(f"Sending workflow {sending_workflow.id} engine run failed: {e}")
            sending_workflow.status = 'failed'
            sending_workflow.completed_at = datetime.utcnow()
            db.session.commit()
            raise
        loop_results = result_context.get('_loop_results') or []
        targets = result_context.get('targets') or []
        sent = result_context.get('_loop_successful', 0)
        failed = result_context.get('_loop_failed', 0)
        total = len(targets) or (sent + failed)
        for i, res in enumerate(loop_results):
            item = res.get('item') or (targets[i] if i < len(targets) else {})
            email = item.get('email', '') if isinstance(item, dict) else ''
            # Only create EmailJob for email targets. Phone-only targets
            # are tracked via SmsJob/CallJob created by sending plugins.
            if not email:
                continue
            job = EmailJob(
                sending_workflow_id=sending_workflow.id,
                target_email=email,
                target_data=item if isinstance(item, dict) else {},
                status='sent' if res.get('success', False) else 'failed',
                error_message=None if res.get('success') else res.get('error'),
                sent_at=datetime.utcnow() if res.get('success') else None,
            )
            db.session.add(job)
        sending_workflow.progress = {
            'current': total,
            'total': total,
            'percentage': 100.0,
        }
        sending_workflow.status = 'completed'
        sending_workflow.completed_at = datetime.utcnow()
        db.session.commit()
        logger.info(f"Sending workflow {sending_workflow.id} completed via engine: sent={sent}, failed={failed}")
        return {
            'status': 'completed',
            'total': total,
            'sent': sent,
            'failed': failed,
            'pending': 0,
        }
    
    def _select_targets(
        self,
        sending_workflow: SendingWorkflow,
        campaign: Campaign
    ) -> List[Dict[str, Any]]:
        """Execute target selector plugin to get targets"""
        config = sending_workflow.target_selection_config
        plugin_type = config.get('plugin_type')
        
        if not plugin_type:
            raise ValueError("target_selection_config.plugin_type is required")
        
        plugin = get_plugin(plugin_type)
        if not plugin:
            raise ValueError(f"Target selection plugin '{plugin_type}' not found")
        
        # Prepare context for target selector
        context = {
            'campaign_id': campaign.id,
            'campaign': campaign.to_dict(),
            'sending_workflow_id': sending_workflow.id
        }
        
        # Execute plugin
        plugin_config = config.get('config', {})
        result_context = plugin.execute(context, plugin_config)
        
        # Extract targets from context
        # Target selector plugin returns 'targets' in context
        if not isinstance(result_context, dict):
            raise ValueError(f"Target selector plugin must return a dict, got {type(result_context)}")
        
        targets = result_context.get('targets', [])
        if not isinstance(targets, list):
            raise ValueError("Target selector plugin must return 'targets' as a list in context")
        
        return targets
    
    def _validate_template(
        self,
        sending_workflow: SendingWorkflow,
        campaign: Campaign
    ) -> Dict[str, Any]:
        """Execute email validator plugin"""
        config = sending_workflow.template_validation_config
        plugin_type = config.get('plugin_type')
        
        if not plugin_type:
            return {'is_valid': True}  # No validation configured
        
        plugin = get_plugin(plugin_type)
        if not plugin:
            logger.warning(f"Template validation plugin '{plugin_type}' not found")
            return {'is_valid': True}
        
        # Prepare context
        context = {
            'campaign_id': campaign.id,
            'campaign': campaign.to_dict(),
            'sending_workflow_id': sending_workflow.id
        }
        
        # Execute plugin - validator expects template_html and template_text in config
        plugin_config = config.get('config', {}).copy()
        plugin_config['template_html'] = campaign.template_html
        plugin_config['template_text'] = ''  # Campaign doesn't have separate text template
        result_context = plugin.execute(context, plugin_config)
        
        # Email validator returns 'email_validation' in context
        validation_result = result_context.get('email_validation', {})
        return {
            'is_valid': validation_result.get('valid', True),
            'errors': validation_result.get('errors', []),
            'warnings': validation_result.get('warnings', [])
        }
    
    def _run_pre_render(
        self,
        sending_workflow: SendingWorkflow,
        campaign: Campaign,
        target: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Run optional pre-render plugin per target; return extra template context or None."""
        pre_render_config = getattr(sending_workflow, 'pre_render_config', None) or {}
        plugin_type = pre_render_config.get('plugin_type') if isinstance(pre_render_config, dict) else None
        if not plugin_type:
            return None
        try:
            campaign_dict = campaign.to_dict()
            context = {
                'campaign': campaign_dict,
                'target': target,
                'target_email': target.get('email'),
                'target_name': target.get('name') or target.get('first_name', ''),
                'uid': campaign.uid,
                'config': campaign_dict.get('config', {}),
                'campaign_id': campaign.id,
                'sending_workflow_id': sending_workflow.id,
                **campaign_dict.get('config', {}),
                **target.get('custom_data', {}),
            }
            plugin = get_plugin(plugin_type)
            plugin_config = pre_render_config.get('config', {}) if isinstance(pre_render_config, dict) else {}
            result = plugin.execute(context, plugin_config)
            if isinstance(result, dict):
                return result
            return None
        except Exception as e:
            logger.warning(f"Pre-render plugin {plugin_type} failed: {e}", exc_info=True)
            return None

    def _render_email(
        self,
        campaign: Campaign,
        target: Dict[str, Any],
        extra_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Render email template with campaign and target data"""
        try:
            # Prepare template context - similar to campaign handler
            campaign_dict = campaign.to_dict()
            template_context = {
                'campaign': campaign_dict,
                'target': target,
                'target_email': target.get('email'),
                'target_name': target.get('name') or target.get('first_name', ''),
                'uid': campaign.uid,
                'config': campaign_dict.get('config', {}),
                **campaign_dict.get('config', {}),  # Make config values available directly
                **target.get('custom_data', {})
            }
            
            # Add template variables from campaign config if present
            template_variables = campaign_dict.get('config', {}).get('template_variables', {})
            template_context.update(template_variables)
            if extra_context:
                template_context.update(extra_context)
            
            # Render HTML template
            # For outbound campaigns, template_html can be None - workflows should generate content
            if not campaign.template_html:
                # If no template_html, check if workflow has generated email content
                # This allows workflows to generate email content without requiring a template
                if campaign.campaign_type == 'outbound':
                    # For outbound campaigns without templates, workflows must generate content
                    # Check if email content was generated by workflow plugins
                    rendered_html = template_context.get('email_html') or template_context.get('html', '')
                    if not rendered_html:
                        # Fallback: use a simple default if workflow didn't generate content
                        rendered_html = f"<html><body><h1>{campaign.name}</h1><p>Email content should be generated by workflow plugins.</p></body></html>"
                else:
                    # For inbound campaigns, template_html is required
                    raise ValueError("Campaign template HTML is empty")
            else:
                # Render template if available
                template = Template(campaign.template_html)
                rendered_html = template.render(**template_context)
            
            # Extract subject from rendered template or use campaign name
            # Try to find subject in template variables or use campaign name
            subject = template_context.get('subject')
            if not subject:
                # Check if subject is in campaign config
                subject = campaign_dict.get('config', {}).get('subject') or campaign.name or 'Email'
            
            return {
                'subject': subject,
                'html': rendered_html,
                'text': self._html_to_text(rendered_html)  # Simple conversion
            }
            
        except Exception as e:
            logger.error(f"Error rendering email template: {e}", exc_info=True)
            raise ValueError(f"Template rendering failed: {e}")
    
    def _html_to_text(self, html: str) -> str:
        """Simple HTML to text conversion"""
        # Very basic conversion - can be enhanced later
        import re
        if not html:
            return ''
        text = re.sub(r'<[^>]+>', '', html)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def _send_email(
        self,
        sending_workflow: SendingWorkflow,
        target: Dict[str, Any],
        rendered_email: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute sending plugin to send email"""
        config = sending_workflow.sending_method_config
        plugin_type = config.get('plugin_type')
        
        if not plugin_type:
            raise ValueError("sending_method_config.plugin_type is required")
        
        plugin = get_plugin(plugin_type)
        if not plugin:
            raise ValueError(f"Sending plugin '{plugin_type}' not found")
        
        # Prepare context for sending plugin
        # The plugin config can use template variables like {{to_email}}, {{subject}}, etc.
        # We provide all data in context for the plugin to resolve
        context = {
            'target_email': target.get('email'),
            'target': target,
            'to_email': target.get('email'),  # SMTP plugin expects 'to_email' in context
            'subject': rendered_email.get('subject'),
            'body_html': rendered_email.get('html'),
            'body_text': rendered_email.get('text'),
            'email_subject': rendered_email.get('subject'),  # Also provide as email_subject
            'email_html': rendered_email.get('html'),  # Also provide as email_html
            'email_text': rendered_email.get('text'),  # Also provide as email_text
            'campaign_id': sending_workflow.campaign_id,
            'sending_workflow_id': sending_workflow.id,
            'campaign': sending_workflow.campaign.to_dict() if sending_workflow.campaign else {}
        }
        
        # Execute plugin
        plugin_config = config.get('config', {})
        
        # If plugin config doesn't specify to_email/subject/body, use rendered values
        # This allows plugins to either use template variables or direct values
        if 'to_email' not in plugin_config:
            plugin_config['to_email'] = '{{to_email}}'  # Will be resolved from context
        if 'subject' not in plugin_config:
            plugin_config['subject'] = '{{subject}}'  # Will be resolved from context
        if 'body_html' not in plugin_config and rendered_email.get('html'):
            plugin_config['body_html'] = '{{body_html}}'  # Will be resolved from context
        if 'body_text' not in plugin_config and rendered_email.get('text'):
            plugin_config['body_text'] = '{{body_text}}'  # Will be resolved from context
        
        try:
            result_context = plugin.execute(context, plugin_config)
            
            # Validate plugin return value
            if not isinstance(result_context, dict):
                logger.error(f"Sending plugin '{plugin_type}' returned invalid type: {type(result_context)}")
                return {'success': False, 'error': 'Plugin returned invalid response'}
            
            # Check if plugin indicates success
            # SMTP sender plugin returns '_email_sent' dict on success
            if result_context.get('_email_sent'):
                return {'success': True}
            elif result_context.get('_email_error'):
                error = result_context.get('_email_error', 'Unknown error')
                return {'success': False, 'error': error}
            else:
                # Check for any error indicators
                if '_error' in result_context or 'error' in result_context:
                    error = result_context.get('_error') or result_context.get('error', 'Unknown error')
                    return {'success': False, 'error': str(error)}
                # Assume success if no error indicators
                return {'success': True}
                
        except Exception as e:
            logger.error(f"Sending plugin '{plugin_type}' execution failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
    
    def _create_email_job(
        self,
        sending_workflow: SendingWorkflow,
        target: Dict[str, Any]
    ) -> EmailJob:
        """Create an email job record"""
        email_job = EmailJob(
            sending_workflow_id=sending_workflow.id,
            target_email=target.get('email'),
            target_data=target,
            status='pending'
        )
        db.session.add(email_job)
        db.session.flush()
        return email_job
    
    def _update_progress(
        self,
        sending_workflow: SendingWorkflow,
        results: Dict[str, Any],
        total_targets: int
    ):
        """Update progress tracking for sending workflow"""
        sending_workflow.progress = {
            'current': results['sent'] + results['failed'],
            'total': total_targets,
            'percentage': round(((results['sent'] + results['failed']) / total_targets) * 100, 2) if total_targets > 0 else 0.0
        }
    
    def _apply_rate_limit(self, sending_workflow: SendingWorkflow):
        """Apply rate limiting based on workflow configuration"""
        # Get rate limit config from workflow
        rate_limit_config = sending_workflow.rate_limit_config or {}
        emails_per_minute = rate_limit_config.get('emails_per_minute', 60)
        emails_per_hour = rate_limit_config.get('emails_per_hour', 1000)
        
        # Use database-backed rate limiter
        from workflows.rate_limiter import get_rate_limiter
        rate_limiter = get_rate_limiter()
        
        check_result = rate_limiter.check_rate_limit(
            sending_workflow.id,
            emails_per_minute,
            emails_per_hour
        )
        
        if not check_result.get('allowed', True):
            wait_seconds = check_result.get('wait_seconds', 0)
            if wait_seconds > 0:
                logger.info(f"Rate limit: waiting {wait_seconds} seconds")
                time.sleep(wait_seconds)

# Global executor instance
_executor = None

def get_executor() -> SendingWorkflowExecutor:
    """Get global sending workflow executor instance"""
    global _executor
    if _executor is None:
        _executor = SendingWorkflowExecutor()
    return _executor

def execute_sending_workflow(sending_workflow_id: int, dry_run: bool = False) -> Dict[str, Any]:
    """Execute a sending workflow"""
    return get_executor().execute_sending_workflow(sending_workflow_id, dry_run)

