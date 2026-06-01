"""
Sending workflow service for managing email sending workflows
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from shared.database import db, SendingWorkflow, EmailJob, Campaign, User, Workflow
from api.services.plugin_service import PluginService
import logging

logger = logging.getLogger(__name__)

class SendingWorkflowService:
    """Service for sending workflow operations"""
    
    def __init__(self):
        self.plugin_service = PluginService()
    
    def create_sending_workflow(
        self,
        name: str,
        campaign_id: Optional[int] = None,
        target_selection_config: Optional[Dict[str, Any]] = None,
        sending_method_config: Optional[Dict[str, Any]] = None,
        description: str = "",
        template_validation_config: Optional[Dict[str, Any]] = None,
        pre_render_config: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[int] = None,
        user: Optional[User] = None
    ) -> SendingWorkflow:
        """Create a new sending workflow (workflow_id from builder required)."""
        try:
            if workflow_id is None:
                raise ValueError("workflow_id is required")
            target_selection_config = target_selection_config or {}
            sending_method_config = sending_method_config or {}
            if campaign_id is not None:
                campaign = Campaign.query.get(campaign_id)
                if not campaign:
                    raise ValueError(f"Campaign {campaign_id} not found")
                if campaign.campaign_type != 'outbound':
                    raise ValueError("Campaign must be an outbound campaign")
            w = Workflow.query.get(workflow_id)
            if not w:
                raise ValueError(f"Workflow {workflow_id} not found")
            if w.workflow_type != 'sending':
                raise ValueError("Workflow must be a sending workflow")
            validation_errors = self.validate_sending_workflow(
                target_selection_config,
                sending_method_config,
                template_validation_config,
                pre_render_config=pre_render_config,
                campaign_id=campaign_id,
                workflow_id=workflow_id,
            )
            if validation_errors:
                raise ValueError(f"Validation failed: {', '.join(validation_errors)}")
            
            sending_workflow = SendingWorkflow(
                name=name,
                description=description,
                campaign_id=campaign_id,
                workflow_id=workflow_id,
                target_selection_config=target_selection_config,
                sending_method_config=sending_method_config,
                template_validation_config=template_validation_config or {},
                pre_render_config=pre_render_config or {},
                status='draft',
                created_by_id=user.id if user else None
            )
            
            db.session.add(sending_workflow)
            db.session.commit()
            
            logger.info(f"Sending workflow created: {name} (ID: {sending_workflow.id})")
            return sending_workflow
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to create sending workflow: {e}")
            raise
    
    def update_sending_workflow(
        self,
        sending_workflow: SendingWorkflow,
        updates: Dict[str, Any]
    ) -> SendingWorkflow:
        """Update a sending workflow"""
        try:
            if 'name' in updates:
                sending_workflow.name = updates['name']
            if 'description' in updates:
                sending_workflow.description = updates['description']
            if 'campaign_id' in updates:
                cid = updates['campaign_id']
                if cid is not None:
                    campaign = Campaign.query.get(cid)
                    if not campaign:
                        raise ValueError(f"Campaign {cid} not found")
                    if campaign.campaign_type != 'outbound':
                        raise ValueError("Campaign must be an outbound campaign")
                sending_workflow.campaign_id = cid
            if 'workflow_id' in updates:
                wid = updates['workflow_id']
                if wid is not None:
                    w = Workflow.query.get(wid)
                    if not w:
                        raise ValueError(f"Workflow {wid} not found")
                    if w.workflow_type != 'sending':
                        raise ValueError("Workflow must be a sending workflow")
                sending_workflow.workflow_id = wid
            if 'target_selection_config' in updates:
                sending_workflow.target_selection_config = updates['target_selection_config']
            if 'sending_method_config' in updates:
                sending_workflow.sending_method_config = updates['sending_method_config']
            if 'template_validation_config' in updates:
                sending_workflow.template_validation_config = updates['template_validation_config']
            if 'pre_render_config' in updates:
                sending_workflow.pre_render_config = updates['pre_render_config']
            if 'status' in updates:
                sending_workflow.status = updates['status']
            if 'scheduled_at' in updates:
                sending_workflow.scheduled_at = updates['scheduled_at']
            
            # Validate if configs are being updated (only when not using workflow graph)
            if any(k in updates for k in ['target_selection_config', 'sending_method_config', 'template_validation_config', 'pre_render_config', 'campaign_id', 'workflow_id']):
                validation_errors = self.validate_sending_workflow(
                    sending_workflow.target_selection_config or {},
                    sending_workflow.sending_method_config or {},
                    sending_workflow.template_validation_config,
                    pre_render_config=getattr(sending_workflow, 'pre_render_config', None),
                    campaign_id=sending_workflow.campaign_id,
                    workflow_id=getattr(sending_workflow, 'workflow_id', None),
                )
                if validation_errors:
                    raise ValueError(f"Validation failed: {', '.join(validation_errors)}")
            
            sending_workflow.updated_at = datetime.utcnow()
            db.session.commit()
            
            logger.info(f"Sending workflow updated: {sending_workflow.name} (ID: {sending_workflow.id})")
            return sending_workflow
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to update sending workflow: {e}")
            raise
    
    def delete_sending_workflow(self, sending_workflow: SendingWorkflow):
        """Delete a sending workflow"""
        try:
            # Check if workflow is running
            if sending_workflow.status == 'running':
                raise ValueError("Cannot delete a running workflow")
            
            db.session.delete(sending_workflow)
            db.session.commit()
            
            logger.info(f"Sending workflow deleted: {sending_workflow.name} (ID: {sending_workflow.id})")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to delete sending workflow: {e}")
            raise
    
    def validate_sending_workflow(
        self,
        target_selection_config: Dict[str, Any],
        sending_method_config: Dict[str, Any],
        template_validation_config: Optional[Dict[str, Any]] = None,
        pre_render_config: Optional[Dict[str, Any]] = None,
        campaign_id: Optional[int] = None,
        workflow_id: Optional[int] = None,
    ) -> List[str]:
        """Validate sending workflow configuration. When workflow_id is set, only campaign outbound and workflow validity are checked."""
        errors = []
        if campaign_id is not None:
            campaign = Campaign.query.get(campaign_id)
            if not campaign:
                errors.append(f"Campaign {campaign_id} not found")
            elif campaign.campaign_type != 'outbound':
                errors.append("Campaign must be an outbound campaign")
        if workflow_id is not None:
            w = Workflow.query.get(workflow_id)
            if not w:
                errors.append(f"Workflow {workflow_id} not found")
            elif w.workflow_type != 'sending':
                errors.append("Workflow must be a sending workflow")
        return errors
    
    def get_sending_workflow(self, workflow_id: int) -> Optional[SendingWorkflow]:
        """Get a sending workflow by ID"""
        return SendingWorkflow.query.get(workflow_id)
    
    def list_sending_workflows(
        self,
        page: int = 1,
        per_page: int = 20,
        status: Optional[str] = None,
        campaign_id: Optional[int] = None,
        user_id: Optional[int] = None,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """List sending workflows with filters"""
        query = SendingWorkflow.query
        
        if status:
            query = query.filter(SendingWorkflow.status == status)
        if campaign_id:
            query = query.filter(SendingWorkflow.campaign_id == campaign_id)
        if user_id:
            query = query.filter(SendingWorkflow.created_by_id == user_id)
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                db.or_(
                    SendingWorkflow.name.ilike(search_term),
                    SendingWorkflow.description.ilike(search_term)
                )
            )
        
        pagination = query.order_by(SendingWorkflow.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return {
            'workflows': [w.to_dict() for w in pagination.items],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages
            }
        }
    
    def get_workflow_stats(self, workflow_id: int) -> Dict[str, Any]:
        """Get statistics for a sending workflow"""
        workflow = self.get_sending_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Sending workflow {workflow_id} not found")
        
        jobs = EmailJob.query.filter_by(sending_workflow_id=workflow_id).all()
        
        stats = {
            'total': len(jobs),
            'pending': sum(1 for j in jobs if j.status == 'pending'),
            'sent': sum(1 for j in jobs if j.status == 'sent'),
            'failed': sum(1 for j in jobs if j.status == 'failed'),
            'retrying': sum(1 for j in jobs if j.status == 'retrying'),
            'success_rate': 0.0
        }
        
        if stats['total'] > 0:
            stats['success_rate'] = (stats['sent'] / stats['total']) * 100
        
        return stats

