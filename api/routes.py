"""
API routes for admin interface
"""
from functools import wraps
from pathlib import Path
from flask import Blueprint, request, jsonify, g, send_file
from shared.database import db, Campaign, User, Event, TrackedUser, TrackingEvent, Template, Asset, Setting, Plugin, Workflow, Notification, EmailJob, CallJob, SendingWorkflow, CredentialProxyJob
from shared.auth import require_auth, get_current_user
from shared.errors import ValidationException, CampaignNotFoundException
# Removed handler imports - campaigns now use workflows
from api.models.models import CampaignCreateRequest, CampaignUpdateRequest, TrackedUserRequest, TemplateCreateRequest, TemplateUpdateRequest
from api.services.services import CampaignService, TemplateService, StatisticsService
from api.services.plugin_service import PluginService
from api.services.sending_workflow_service import SendingWorkflowService
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__)

# Initialize services
campaign_service = CampaignService()
template_service = TemplateService()
stats_service = StatisticsService()
plugin_service = PluginService()
sending_workflow_service = SendingWorkflowService()

@api_bp.before_request
def load_user():
    """Load current user for API requests"""
    pass

# Campaign endpoints
# Note: GET endpoints are automatically exempt from CSRF by Flask-WTF
# State-changing operations (POST, PUT, DELETE) require CSRF tokens

@api_bp.route('/campaigns', methods=['GET'])

@require_auth
def list_campaigns():
    """
    List all campaigns
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number for pagination
      - name: per_page
        in: query
        type: integer
        default: 10
        maximum: 100
        description: Number of items per page
      - name: status
        in: query
        type: string
        enum: [draft, active, paused, completed]
        description: Filter by campaign status
      - name: workflow_id
        in: query
        type: integer
        description: Filter by workflow ID
    responses:
      200:
        description: List of campaigns
        schema:
          type: object
          properties:
            campaigns:
              type: array
              items:
                $ref: '#/definitions/Campaign'
            pagination:
              $ref: '#/definitions/Pagination'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 10, type=int), 100)
        status = request.args.get('status')
        workflow_id = request.args.get('workflow_id', type=int)
        campaign_type = request.args.get('campaign_type')  # e.g. 'outbound' for sending workflow campaign selector
        
        query = Campaign.query

        if status:
            query = query.filter(Campaign.status == status)
        else:
            # Hide soft-deleted campaigns unless explicitly requested
            query = query.filter(Campaign.status != 'deleted')
        if campaign_type:
            query = query.filter(Campaign.campaign_type == campaign_type)
        if workflow_id:
            # Filter by campaigns that use this workflow for GET or POST
            query = query.filter(
                (Campaign.get_workflow_id == workflow_id) | 
                (Campaign.post_workflow_id == workflow_id)
            )
        
        campaigns = query.order_by(Campaign.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'campaigns': [campaign.to_dict() for campaign in campaigns.items],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': campaigns.total,
                'pages': campaigns.pages,
                'has_next': campaigns.has_next,
                'has_prev': campaigns.has_prev
            }
        })
        
    except Exception as e:
        logger.exception("Error listing campaigns")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns', methods=['POST'])
@require_auth
def create_campaign():
    """
    Create a new campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - in: body
        name: campaign
        required: true
        schema:
          type: object
          required:
            - name
            - campaign_type
          properties:
            name:
              type: string
              description: Campaign name
            description:
              type: string
              description: Campaign description
            campaign_type:
              type: string
              enum: [inbound, outbound]
              description: Type of campaign
            uid:
              type: string
              description: Unique identifier (for inbound campaigns)
            template_id:
              type: integer
              description: Template ID
            get_workflow_id:
              type: integer
              description: GET workflow ID
            post_workflow_id:
              type: integer
              description: POST workflow ID
            config:
              type: object
              description: Campaign configuration
            variables:
              type: object
              description: Campaign variables
    responses:
      201:
        description: Campaign created successfully
        schema:
          $ref: '#/definitions/Campaign'
      400:
        description: Validation error
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        data = request.get_json()
        if not data:
            raise ValidationException("Request body is required")
        
        # Validate request data
        campaign_request = CampaignCreateRequest(**data)
        
        # Create campaign
        campaign = campaign_service.create_campaign(campaign_request, get_current_user())
        
        return jsonify(campaign.to_dict()), 201
        
    except ValidationException as e:
        return jsonify({'error': e.message, 'details': e.errors}), 400
    except Exception as e:
        logger.exception("Error creating campaign")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>', methods=['GET'])

@require_auth
def get_campaign(campaign_id):
    """
    Get a specific campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
    responses:
      200:
        description: Campaign details
        schema:
          $ref: '#/definitions/Campaign'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        return jsonify(campaign.to_dict(include_template=True))
        
    except Exception as e:
        logger.exception(f"Error getting campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>', methods=['PUT'])
@require_auth
def update_campaign(campaign_id):
    """
    Update an existing campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
      - in: body
        name: campaign
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
            description:
              type: string
            status:
              type: string
              enum: [draft, active, paused, completed]
            config:
              type: object
            variables:
              type: object
    responses:
      200:
        description: Campaign updated successfully
        schema:
          $ref: '#/definitions/Campaign'
      400:
        description: Validation error
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        
        # Check permissions
        current_user = get_current_user()
        if campaign.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get JSON data
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body is required'}), 400
        
        # Validate request data
        try:
            campaign_request = CampaignUpdateRequest(**data)
        except Exception as e:
            return jsonify({'error': f'Validation error: {str(e)}'}), 400
        
        # Update campaign using service
        updated_campaign = campaign_service.update_campaign(campaign, campaign_request)
        
        logger.info(f"Campaign '{updated_campaign.name}' (ID: {campaign_id}) updated by user {current_user.username}")
        
        return jsonify(updated_campaign.to_dict(include_template=True)), 200
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error updating campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/override-phishing', methods=['POST'])
@require_auth
def override_campaign_phishing(campaign_id):
    """
    Override phishing detection for a campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
    responses:
      200:
        description: Phishing override set successfully
        schema:
          $ref: '#/definitions/Campaign'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from datetime import datetime
        
        campaign = Campaign.query.get_or_404(campaign_id)
        
        # Check ownership or admin rights
        current_user = get_current_user()
        if campaign.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        # Set override
        campaign.phishing_override = True
        campaign.phishing_approved_by_id = current_user.id
        campaign.phishing_approved_at = datetime.utcnow()
        
        db.session.commit()
        
        logger.info(f"Phishing override set for campaign {campaign_id} by user {current_user.username}")
        return jsonify(campaign.to_dict())
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error overriding phishing for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/override-phishing', methods=['DELETE'])
@require_auth
def remove_campaign_phishing_override(campaign_id):
    """
    Remove phishing detection override for a campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
    responses:
      200:
        description: Phishing override removed successfully
        schema:
          $ref: '#/definitions/Campaign'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        
        # Check ownership or admin rights
        current_user = get_current_user()
        if campaign.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        # Remove override
        campaign.phishing_override = False
        campaign.phishing_approved_by_id = None
        campaign.phishing_approved_at = None
        
        db.session.commit()
        
        logger.info(f"Phishing override removed for campaign {campaign_id} by user {current_user.username}")
        return jsonify(campaign.to_dict())
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error removing phishing override for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/start', methods=['POST'])
@require_auth
def start_campaign(campaign_id):
    """
    Start a campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
    responses:
      200:
        description: Campaign started successfully
        schema:
          $ref: '#/definitions/Campaign'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        campaign_service.start_campaign(campaign)
        
        return jsonify(campaign.to_dict())
        
    except Exception as e:
        logger.exception(f"Error starting campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/stop', methods=['POST'])
@require_auth
def stop_campaign(campaign_id):
    """
    Stop a campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
    responses:
      200:
        description: Campaign stopped successfully
        schema:
          $ref: '#/definitions/Campaign'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        campaign_service.stop_campaign(campaign)
        
        return jsonify(campaign.to_dict())
        
    except Exception as e:
        logger.exception(f"Error stopping campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/stats', methods=['GET'])

@require_auth
def get_campaign_stats(campaign_id):
    """
    Get campaign statistics
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
    responses:
      200:
        description: Campaign statistics
        schema:
          $ref: '#/definitions/CampaignStats'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        stats = stats_service.get_campaign_stats(campaign_id)
        
        return jsonify(stats)
        
    except Exception as e:
        logger.exception(f"Error getting stats for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/reporting/series', methods=['GET'])
@require_auth
def get_campaign_reporting_series(campaign_id):
    """
    Get time-series data for campaign reporting charts.
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
      - name: from
        in: query
        type: string
        format: date
        description: Start date (YYYY-MM-DD). Defaults to 30 days before to.
      - name: to
        in: query
        type: string
        format: date
        description: End date (YYYY-MM-DD). Defaults to today.
      - name: group_by
        in: query
        type: string
        default: day
        description: Grouping (only day supported).
    responses:
      200:
        description: Time-series with labels and inbound/outbound series
      401:
        description: Unauthorized
      404:
        description: Campaign not found
      500:
        description: Internal server error
    """
    try:
        from datetime import datetime, timedelta, date as date_type

        campaign = Campaign.query.get_or_404(campaign_id)
        to_arg = request.args.get('to')
        from_arg = request.args.get('from')
        to_date = None
        from_date = None
        if to_arg:
            try:
                to_date = datetime.strptime(to_arg, '%Y-%m-%d').date()
            except ValueError:
                pass
        if from_arg:
            try:
                from_date = datetime.strptime(from_arg, '%Y-%m-%d').date()
            except ValueError:
                pass
        data = stats_service.get_campaign_series(
            campaign_id,
            from_date=from_date,
            to_date=to_date,
            group_by=request.args.get('group_by', 'day'),
        )
        return jsonify(data)
    except Exception as e:
        logger.exception(f"Error getting reporting series for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/events', methods=['GET'])

@require_auth
def get_campaign_events(campaign_id):
    """
    Get campaign events
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number for pagination
      - name: per_page
        in: query
        type: integer
        default: 50
        maximum: 200
        description: Number of items per page
      - name: event_type
        in: query
        type: string
        description: Filter by event type
    responses:
      200:
        description: List of campaign events
        schema:
          type: object
          properties:
            events:
              type: array
              items:
                $ref: '#/definitions/Event'
            pagination:
              $ref: '#/definitions/Pagination'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 200)
        event_type = request.args.get('event_type')
        
        query = Event.query.filter(Event.campaign_id == campaign_id)
        
        if event_type:
            query = query.filter(Event.event_type == event_type)
        
        events = query.order_by(Event.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'events': [event.to_dict() for event in events.items],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': events.total,
                'pages': events.pages,
                'has_next': events.has_next,
                'has_prev': events.has_prev
            }
        })
        
    except Exception as e:
        logger.exception(f"Error getting events for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/events', methods=['DELETE'])
@require_auth
def clear_campaign_events(campaign_id):
    """
    Clear all events for a campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
    responses:
      200:
        description: Events cleared successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            deleted:
              type: integer
              description: Number of events deleted
      401:
        description: Unauthorized
      404:
        description: Campaign not found
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)

        deleted = Event.query.filter(Event.campaign_id == campaign_id).delete()
        db.session.commit()

        logger.info(f"Cleared {deleted} events for campaign '{campaign.name}' (ID: {campaign_id})")

        return jsonify({
            'success': True,
            'deleted': deleted,
            'message': f'Cleared {deleted} event{"s" if deleted != 1 else ""}'
        })

    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error clearing events for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/send-logs', methods=['GET'])
@require_auth
def get_campaign_send_logs(campaign_id):
    """
    Get all send logs (emails and calls) for a campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
      - name: type
        in: query
        type: string
        enum: [email, call]
        description: Filter by log type (email or call)
      - name: status
        in: query
        type: string
        enum: [pending, sent, completed, failed, retrying]
        description: Filter by status
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number for pagination
      - name: per_page
        in: query
        type: integer
        default: 50
        maximum: 200
        description: Number of items per page
    responses:
      200:
        description: List of send logs
        schema:
          type: object
          properties:
            logs:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: integer
                  type:
                    type: string
                    enum: [email, call]
                  target:
                    type: string
                  status:
                    type: string
                  sent_at:
                    type: string
                    format: date-time
                  error_message:
                    type: string
                  retry_count:
                    type: integer
                  created_at:
                    type: string
                    format: date-time
                  workflow_id:
                    type: integer
                  contact_id:
                    type: string
            pagination:
              type: object
              properties:
                page:
                  type: integer
                per_page:
                  type: integer
                total:
                  type: integer
                pages:
                  type: integer
                has_next:
                  type: boolean
                has_prev:
                  type: boolean
      401:
        description: Unauthorized
      404:
        description: Campaign not found
      500:
        description: Internal server error
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 200)
        log_type = request.args.get('type')  # 'email' or 'call'
        status_filter = request.args.get('status')  # 'pending', 'sent', 'completed', 'failed', 'retrying'
        
        # Get email jobs from all sending workflows for this campaign
        email_jobs_query = EmailJob.query.join(SendingWorkflow).filter(
            SendingWorkflow.campaign_id == campaign_id
        )
        
        if status_filter:
            # Map status filter to email job statuses
            if status_filter == 'sent':
                email_jobs_query = email_jobs_query.filter(EmailJob.status == 'sent')
            elif status_filter == 'completed':
                email_jobs_query = email_jobs_query.filter(EmailJob.status == 'sent')  # Email 'sent' = completed
            elif status_filter == 'failed':
                email_jobs_query = email_jobs_query.filter(EmailJob.status == 'failed')
            elif status_filter == 'pending':
                email_jobs_query = email_jobs_query.filter(EmailJob.status == 'pending')
            elif status_filter == 'retrying':
                email_jobs_query = email_jobs_query.filter(EmailJob.status == 'retrying')
        
        email_jobs = email_jobs_query.all() if log_type != 'call' else []
        
        # Get call jobs
        call_jobs_query = CallJob.query.filter(
            CallJob.campaign_id == campaign_id
        )
        
        if status_filter:
            # Map status filter to call job statuses
            if status_filter == 'completed':
                call_jobs_query = call_jobs_query.filter(CallJob.status == 'completed')
            elif status_filter == 'failed':
                call_jobs_query = call_jobs_query.filter(CallJob.status == 'failed')
            elif status_filter == 'pending':
                call_jobs_query = call_jobs_query.filter(CallJob.status == 'pending')
            elif status_filter == 'retrying':
                call_jobs_query = call_jobs_query.filter(CallJob.status == 'retrying')
            elif status_filter == 'sent':
                # 'sent' doesn't apply to calls, use 'completed' instead
                call_jobs_query = call_jobs_query.filter(CallJob.status == 'completed')
        
        call_jobs = call_jobs_query.all() if log_type != 'email' else []
        
        # Combine and format logs
        logs = []
        for job in email_jobs:
            logs.append({
                'id': job.id,
                'type': 'email',
                'target': job.target_email,
                'status': job.status,
                'sent_at': job.sent_at.isoformat() if job.sent_at else None,
                'error_message': job.error_message,
                'retry_count': job.retry_count,
                'created_at': job.created_at.isoformat() if job.created_at else None,
                'workflow_id': job.sending_workflow_id,
                'contact_id': None
            })
        
        for job in call_jobs:
            logs.append({
                'id': job.id,
                'type': 'call',
                'target': job.target_phone,
                'status': job.status,
                'sent_at': job.completed_at.isoformat() if job.completed_at else None,
                'error_message': job.error_message,
                'retry_count': job.retry_count,
                'created_at': job.created_at.isoformat() if job.created_at else None,
                'workflow_id': job.workflow_id,
                'contact_id': job.contact_id
            })
        
        # Sort by created_at descending
        logs.sort(key=lambda x: x['created_at'] or '', reverse=True)
        
        # Apply pagination
        total = len(logs)
        pages = (total + per_page - 1) // per_page if total > 0 else 0
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_logs = logs[start_idx:end_idx]
        
        return jsonify({
            'logs': paginated_logs,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': pages,
                'has_next': page < pages,
                'has_prev': page > 1
            }
        })
        
    except Exception as e:
        logger.exception(f"Error getting send logs for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

# Template endpoints
@api_bp.route('/templates', methods=['GET'])

@require_auth
def list_templates():
    """
    List all templates
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number for pagination
      - name: per_page
        in: query
        type: integer
        default: 20
        maximum: 100
        description: Number of items per page
      - name: category
        in: query
        type: string
        description: Filter by category
      - name: search
        in: query
        type: string
        description: Search term for name or description
      - name: public_only
        in: query
        type: boolean
        default: false
        description: Show only public templates
    responses:
      200:
        description: List of templates
        schema:
          type: object
          properties:
            templates:
              type: array
              items:
                $ref: '#/definitions/Template'
            pagination:
              $ref: '#/definitions/Pagination'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        category = request.args.get('category')
        search = request.args.get('search')
        public_only = request.args.get('public_only', 'false').lower() == 'true'
        
        query = Template.query
        
        # Filter by category
        if category:
            query = query.filter(Template.category == category)
        
        # Filter by search term (name or description)
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                db.or_(
                    Template.name.ilike(search_term),
                    Template.description.ilike(search_term)
                )
            )
        
        # Filter by public/private
        if public_only:
            query = query.filter(Template.is_public == True)
        else:
            # Show user's templates + public templates
            current_user = get_current_user()
            query = query.filter(
                db.or_(
                    Template.is_public == True,
                    Template.created_by_id == current_user.id
                )
            )
        
        templates = query.order_by(Template.updated_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'templates': [template.to_dict() for template in templates.items],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': templates.total,
                'pages': templates.pages,
                'has_next': templates.has_next,
                'has_prev': templates.has_prev
            }
        })
        
    except Exception as e:
        logger.exception("Error listing templates")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates', methods=['POST'])
@require_auth
def create_template():
    """
    Create a new template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - in: body
        name: template
        required: true
        schema:
          type: object
          required:
            - name
            - template_html
          properties:
            name:
              type: string
              description: Template name
            description:
              type: string
              description: Template description
            template_html:
              type: string
              description: HTML content
            template_type:
              type: string
              enum: [main, captcha, error]
              default: main
            category:
              type: string
            is_public:
              type: boolean
              default: false
    responses:
      201:
        description: Template created successfully
        schema:
          $ref: '#/definitions/Template'
      400:
        description: Validation error
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        data = request.get_json()
        if not data:
            raise ValidationException("Request body is required")
        
        # Validate request data
        template_request = TemplateCreateRequest(**data)
        
        # Validate Jinja2 template syntax
        template_service.validate_jinja_template(template_request.template_html)
        
        # Create template
        template = template_service.create_template(template_request, get_current_user())
        
        return jsonify(template.to_dict()), 201
        
    except ValidationException as e:
        return jsonify({'error': e.message, 'details': e.errors}), 400
    except Exception as e:
        logger.exception("Error creating template")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>', methods=['GET'])

@require_auth
def get_template(template_id):
    """
    Get a specific template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
    responses:
      200:
        description: Template details
        schema:
          $ref: '#/definitions/Template'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        template = Template.query.get_or_404(template_id)
        
        # Check access permissions
        current_user = get_current_user()
        if not template.is_public and template.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        return jsonify(template.to_dict())
        
    except Exception as e:
        logger.exception(f"Error getting template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>', methods=['PUT'])
@require_auth
def update_template(template_id):
    """
    Update an existing template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
      - in: body
        name: template
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
            description:
              type: string
            template_html:
              type: string
            template_type:
              type: string
              enum: [main, captcha, error]
            category:
              type: string
            is_public:
              type: boolean
    responses:
      200:
        description: Template updated successfully
        schema:
          $ref: '#/definitions/Template'
      400:
        description: Validation error
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        template = Template.query.get_or_404(template_id)
        
        # Check ownership or admin rights
        current_user = get_current_user()
        if template.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        data = request.get_json()
        if not data:
            raise ValidationException("Request body is required")
        
        # Validate request data
        template_request = TemplateUpdateRequest(**data)
        
        # Validate Jinja2 template syntax if HTML is being updated
        if template_request.template_html is not None:
            template_service.validate_jinja_template(template_request.template_html)
        
        # Update template
        updated_template = template_service.update_template(template, template_request)
        
        return jsonify(updated_template.to_dict())
        
    except ValidationException as e:
        return jsonify({'error': e.message, 'details': e.errors}), 400
    except Exception as e:
        logger.exception(f"Error updating template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/override-phishing', methods=['POST'])
@require_auth
def override_template_phishing(template_id):
    """
    Override phishing detection for a template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
    responses:
      200:
        description: Phishing override set successfully
        schema:
          $ref: '#/definitions/Template'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from datetime import datetime
        
        template = Template.query.get_or_404(template_id)
        
        # Check ownership or admin rights
        current_user = get_current_user()
        if template.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        # Set override
        template.phishing_override = True
        template.phishing_approved_by_id = current_user.id
        template.phishing_approved_at = datetime.utcnow()
        
        db.session.commit()
        
        logger.info(f"Phishing override set for template {template_id} by user {current_user.username}")
        return jsonify(template.to_dict())
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error overriding phishing for template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/override-phishing', methods=['DELETE'])
@require_auth
def remove_template_phishing_override(template_id):
    """
    Remove phishing detection override for a template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
    responses:
      200:
        description: Phishing override removed successfully
        schema:
          $ref: '#/definitions/Template'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        template = Template.query.get_or_404(template_id)
        
        # Check ownership or admin rights
        current_user = get_current_user()
        if template.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        # Remove override
        template.phishing_override = False
        template.phishing_approved_by_id = None
        template.phishing_approved_at = None
        
        db.session.commit()
        
        logger.info(f"Phishing override removed for template {template_id} by user {current_user.username}")
        return jsonify(template.to_dict())
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error removing phishing override for template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>', methods=['DELETE'])
@require_auth
def delete_template(template_id):
    """
    Delete a template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
    responses:
      204:
        description: Template deleted successfully
      400:
        description: Template in use by campaigns
        schema:
          type: object
          properties:
            error:
              type: string
            campaigns_count:
              type: integer
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        template = Template.query.get_or_404(template_id)
        
        # Check ownership or admin rights
        current_user = get_current_user()
        if template.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        # Check if template is in use by any campaigns
        campaigns_using_template = Campaign.query.filter(
            Campaign.template_id == template_id,
            Campaign.status.in_(['active', 'draft'])
        ).count()
        
        if campaigns_using_template > 0:
            return jsonify({
                'error': 'Cannot delete template in use by active campaigns',
                'campaigns_count': campaigns_using_template
            }), 400
        
        # Delete template
        template_service.delete_template(template)
        
        return '', 204
        
    except Exception as e:
        logger.exception(f"Error deleting template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/clone', methods=['POST'])
@require_auth
def clone_template(template_id):
    """
    Clone a template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID to clone
    responses:
      201:
        description: Template cloned successfully
        schema:
          $ref: '#/definitions/Template'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        template = Template.query.get_or_404(template_id)
        
        # Check access permissions
        current_user = get_current_user()
        if not template.is_public and template.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        # Clone template
        cloned_template = template_service.clone_template(template, current_user)
        
        return jsonify(cloned_template.to_dict()), 201
        
    except Exception as e:
        logger.exception(f"Error cloning template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/preview', methods=['POST'])
@require_auth
def preview_template(template_id):
    """
    Preview template with sample data
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
      - in: body
        name: preview_data
        schema:
          type: object
          properties:
            preview_data:
              type: object
              description: Sample data to use for template rendering
    responses:
      200:
        description: Rendered template preview
        schema:
          type: object
          properties:
            rendered_html:
              type: string
            preview_data:
              type: object
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        template = Template.query.get_or_404(template_id)
        
        # Check access permissions
        current_user = get_current_user()
        if not template.is_public and template.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get preview data from request or use defaults
        data = request.get_json() or {}
        preview_data = data.get('preview_data', {})
        
        # Render template with preview data
        rendered_html = template_service.render_template_preview(template, preview_data)
        
        return jsonify({
            'rendered_html': rendered_html,
            'preview_data': preview_data
        })
        
    except Exception as e:
        logger.exception(f"Error previewing template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/preview-image', methods=['GET'])
@require_auth
def get_template_preview_image(template_id):
    """Serve the saved preview image for a template."""
    try:
        template = Template.query.get_or_404(template_id)
        if not template.preview_image or not template.preview_image.strip():
            return jsonify({'error': 'No preview image'}), 404
        # Security: only allow path under template_previews and filename {id}.png
        rel = template.preview_image.strip()
        if rel != f"template_previews/{template_id}.png":
            return jsonify({'error': 'Invalid preview path'}), 404
        storage_base = Path("storage")
        file_path = storage_base / rel
        if not file_path.is_file():
            return jsonify({'error': 'Preview image not found'}), 404
        return send_file(
            str(file_path),
            mimetype='image/png',
            as_attachment=False,
            max_age=0
        )
    except Exception as e:
        logger.exception(f"Error serving preview image for template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/save-preview-image', methods=['POST'])
@require_auth
def save_template_preview_image_route(template_id):
    """Save a preview image for a template (multipart form with 'image' or 'file')."""
    try:
        template = Template.query.get_or_404(template_id)
        current_user = get_current_user()
        if template.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        file = request.files.get('image') or request.files.get('file')
        if file and file.filename:
            image_data = file.read()
        else:
            if request.content_type and 'image/png' in request.content_type and request.data:
                image_data = bytes(request.data)
            else:
                return jsonify({'error': 'No image provided. Send multipart form with image/file or PNG body.'}), 400
        if not image_data:
            return jsonify({'error': 'Empty image data'}), 400
        updated = template_service.save_template_preview_image(template_id, image_data)
        return jsonify({
            'success': True,
            'preview_image': updated.preview_image,
            'updated_at': updated.updated_at.isoformat() if updated.updated_at else None
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f"Error saving preview image for template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/categories', methods=['GET'])

@require_auth
def get_template_categories():
    """
    Get all template categories
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    responses:
      200:
        description: List of template categories
        schema:
          type: object
          properties:
            categories:
              type: array
              items:
                type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        categories = template_service.get_categories()
        return jsonify({'categories': categories})
        
    except Exception as e:
        logger.exception("Error getting template categories")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/validate', methods=['POST'])
@require_auth
def validate_template():
    """
    Validate template syntax
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - in: body
        name: template
        required: true
        schema:
          type: object
          required:
            - template_html
          properties:
            template_html:
              type: string
              description: Template HTML to validate
            validation_js:
              type: string
              description: Optional JavaScript to validate
    responses:
      200:
        description: Template validation result
        schema:
          type: object
          properties:
            valid:
              type: boolean
            message:
              type: string
            error:
              type: string
            details:
              type: object
      400:
        description: Validation error
        schema:
          type: object
          properties:
            valid:
              type: boolean
            error:
              type: string
            details:
              type: object
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        data = request.get_json()
        if not data or 'template_html' not in data:
            raise ValidationException("template_html is required")
        
        # Validate Jinja2 syntax
        template_service.validate_jinja_template(data['template_html'])
        
        # Validate JS if provided (for future custom validation support)
        if 'validation_js' in data and data['validation_js']:
            template_service.validate_javascript(data['validation_js'])
        
        return jsonify({'valid': True, 'message': 'Template is valid'})
        
    except ValidationException as e:
        return jsonify({'valid': False, 'error': e.message, 'details': e.errors}), 400
    except Exception as e:
        logger.exception("Error validating template")
        return jsonify({'valid': False, 'error': str(e)}), 500

# Campaign types endpoint
# Campaign types endpoint removed - campaigns now use workflows

# Dashboard/statistics endpoints
@api_bp.route('/dashboard/overview', methods=['GET'])

@require_auth
def dashboard_overview():
    """
    Get dashboard overview statistics
    ---
    tags:
      - Dashboard
    security:
      - Bearer: []
    responses:
      200:
        description: Dashboard overview statistics
        schema:
          type: object
          properties:
            total_campaigns:
              type: integer
            active_campaigns:
              type: integer
            total_templates:
              type: integer
            total_workflows:
              type: integer
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        overview = stats_service.get_dashboard_overview()
        return jsonify(overview)
        
    except Exception as e:
        logger.exception("Error getting dashboard overview")
        return jsonify({'error': str(e)}), 500

# Tracking endpoints
@api_bp.route('/campaigns/<int:campaign_id>/tracking/users', methods=['GET'])

@require_auth
def get_tracked_users(campaign_id):
    """
    Get tracked users for a campaign
    ---
    tags:
      - Tracking
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number for pagination
      - name: per_page
        in: query
        type: integer
        default: 50
        maximum: 200
        description: Number of items per page
    responses:
      200:
        description: List of tracked users
        schema:
          type: object
          properties:
            tracked_users:
              type: array
              items:
                $ref: '#/definitions/TrackedUser'
            pagination:
              $ref: '#/definitions/Pagination'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 200)
        
        tracked_users = TrackedUser.query.filter(
            TrackedUser.campaign_id == campaign_id
        ).order_by(TrackedUser.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'tracked_users': [user.to_dict() for user in tracked_users.items],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': tracked_users.total,
                'pages': tracked_users.pages
            }
        })
        
    except Exception as e:
        logger.exception(f"Error getting tracked users for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/tracking/users', methods=['POST'])
@require_auth
def add_tracked_users(campaign_id):
    """
    Add tracked users to a campaign
    ---
    tags:
      - Tracking
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
      - in: body
        name: users
        required: true
        schema:
          type: object
          required:
            - users
          properties:
            users:
              type: array
              items:
                type: object
                required:
                  - email
                properties:
                  email:
                    type: string
                    format: email
                  first_name:
                    type: string
                  last_name:
                    type: string
                  department:
                    type: string
    responses:
      201:
        description: Users added successfully
        schema:
          type: object
          properties:
            added_users:
              type: array
              items:
                $ref: '#/definitions/TrackedUser'
            errors:
              type: array
              items:
                type: string
            total_added:
              type: integer
            total_errors:
              type: integer
      400:
        description: Validation error
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        data = request.get_json()
        
        if not data or 'users' not in data:
            raise ValidationException("Users list is required")
        
        added_users = []
        errors = []
        
        for user_data in data['users']:
            try:
                email = user_data.get('email', '').strip().lower()
                if not email:
                    errors.append("Email is required for all users")
                    continue
                
                # Check if user already exists for this campaign
                existing = TrackedUser.query.filter_by(
                    campaign_id=campaign_id,
                    email=email
                ).first()
                
                if existing:
                    errors.append(f"User {email} already exists in campaign")
                    continue
                
                tracked_user = TrackedUser(
                    campaign_id=campaign_id,
                    email=email,
                    first_name=user_data.get('first_name', '').strip(),
                    last_name=user_data.get('last_name', '').strip(),
                    department=user_data.get('department', '').strip()
                )
                
                db.session.add(tracked_user)
                added_users.append(tracked_user.to_dict())
                
            except Exception as e:
                errors.append(f"Error adding user {user_data.get('email', 'unknown')}: {str(e)}")
        
        if added_users:
            db.session.commit()
        
        return jsonify({
            'added_users': added_users,
            'errors': errors,
            'total_added': len(added_users),
            'total_errors': len(errors)
        }), 201 if added_users else 400
        
    except ValidationException as e:
        return jsonify({'error': e.message}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error adding tracked users to campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/tracking/links', methods=['POST'])
@require_auth
def generate_tracking_links(campaign_id):
    """
    Generate tracking links for a campaign
    ---
    tags:
      - Tracking
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
      - in: body
        name: link_config
        schema:
          type: object
          properties:
            link_type:
              type: string
              enum: [campaign, pixel, button]
              default: campaign
            target_url:
              type: string
              description: Target URL for button links
            tracking_id:
              type: string
              description: Specific tracking ID to generate links for
    responses:
      200:
        description: Tracking links generated
        schema:
          type: object
          properties:
            campaign_id:
              type: integer
            campaign_uid:
              type: string
            links:
              type: object
              properties:
                campaign_link:
                  type: string
                tracking_pixel:
                  type: string
                button_link:
                  type: string
            base_url:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign or tracking ID not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        data = request.get_json()
        
        link_type = data.get('link_type', 'campaign')  # campaign, pixel, button
        target_url = data.get('target_url', '')
        tracking_id = data.get('tracking_id')
        
        base_url = request.host_url.rstrip('/')
        campaign_url = f"{base_url}/{campaign.uid}"
        
        links = {}
        
        if tracking_id:
            # Generate links for specific user
            tracked_user = TrackedUser.query.filter_by(
                campaign_id=campaign_id,
                tracking_id=tracking_id
            ).first()
            
            if not tracked_user:
                return jsonify({'error': 'Tracking ID not found'}), 404
            
            if getattr(campaign, 'gate_enabled', False) and getattr(campaign, 'gate_token', None):
                gate_param = getattr(campaign, 'gate_param_name', 'rid') or 'rid'
                links['campaign_link'] = f"{campaign_url}?{gate_param}={campaign.gate_token}&t={tracking_id}"
            else:
                links['campaign_link'] = f"{campaign_url}?t={tracking_id}"
            links['tracking_pixel'] = f"{campaign_url}/track/{tracking_id}/email_opened"

            if target_url:
                links['button_link'] = f"{campaign_url}/track/{tracking_id}/button_clicked?target={target_url}"
        else:
            # Generate generic links
            if getattr(campaign, 'gate_enabled', False) and getattr(campaign, 'gate_token', None):
                gate_param = getattr(campaign, 'gate_param_name', 'rid') or 'rid'
                links['campaign_link'] = f"{campaign_url}?{gate_param}={campaign.gate_token}"
            else:
                links['campaign_link'] = campaign_url
            links['tracking_pixel'] = f"{campaign_url}/track/generic/page_viewed"
        
        return jsonify({
            'campaign_id': campaign_id,
            'campaign_uid': campaign.uid,
            'links': links,
            'base_url': base_url
        })
        
    except Exception as e:
        logger.exception(f"Error generating tracking links for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/tracking/events', methods=['GET'])

@require_auth
def get_tracking_events(campaign_id):
    """
    Get tracking events for a campaign
    ---
    tags:
      - Tracking
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number for pagination
      - name: per_page
        in: query
        type: integer
        default: 100
        maximum: 500
        description: Number of items per page
      - name: event_type
        in: query
        type: string
        description: Filter by event type
      - name: tracking_id
        in: query
        type: string
        description: Filter by tracking ID
    responses:
      200:
        description: List of tracking events
        schema:
          type: object
          properties:
            events:
              type: array
              items:
                $ref: '#/definitions/TrackingEvent'
            pagination:
              $ref: '#/definitions/Pagination'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        campaign = Campaign.query.get_or_404(campaign_id)
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 100, type=int), 500)
        event_type = request.args.get('event_type')
        tracking_id = request.args.get('tracking_id')
        
        # Get tracking events through joins
        query = db.session.query(TrackingEvent).join(TrackedUser).filter(
            TrackedUser.campaign_id == campaign_id
        )
        
        if event_type:
            query = query.filter(TrackingEvent.event_type == event_type)
        
        if tracking_id:
            query = query.filter(TrackedUser.tracking_id == tracking_id)
        
        events = query.order_by(TrackingEvent.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Include user info in events
        events_with_users = []
        for event in events.items:
            event_dict = event.to_dict()
            event_dict['user'] = event.tracked_user.to_dict()
            events_with_users.append(event_dict)
        
        return jsonify({
            'events': events_with_users,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': events.total,
                'pages': events.pages
            }
        })
        
    except Exception as e:
        logger.exception(f"Error getting tracking events for campaign {campaign_id}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/campaigns/<int:campaign_id>/credential-proxy-jobs', methods=['GET'])
@require_auth
def list_campaign_credential_proxy_jobs(campaign_id):
    """List credential proxy jobs for a campaign. Returns full job data; UI masks credentials/cookies."""
    try:
        Campaign.query.get_or_404(campaign_id)
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 50)
        pagination = CredentialProxyJob.query.filter_by(campaign_id=campaign_id).order_by(
            CredentialProxyJob.created_at.desc()
        ).paginate(page=page, per_page=per_page, error_out=False)
        jobs = [j.to_dict() for j in pagination.items]
        return jsonify({
            'jobs': jobs,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages
            }
        })
    except Exception as e:
        logger.exception(f"Error listing credential proxy jobs for campaign {campaign_id}: {e}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/credential-proxy-jobs', methods=['GET'])
@require_auth
def list_credential_proxy_jobs():
    """List all credential proxy jobs (optional campaign_id filter). Returns full job data; UI masks credentials/cookies."""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 50)
        campaign_id = request.args.get('campaign_id', type=int)
        query = CredentialProxyJob.query.order_by(CredentialProxyJob.created_at.desc())
        if campaign_id is not None:
            query = query.filter_by(campaign_id=campaign_id)
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        jobs = [j.to_dict() for j in pagination.items]
        return jsonify({
            'jobs': jobs,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages
            }
        })
    except Exception as e:
        logger.exception("Error listing credential proxy jobs: %s", e)
        return jsonify({'error': str(e)}), 500


@api_bp.route('/sync-proxy-sessions', methods=['GET'])
@require_auth
def list_sync_proxy_sessions():
    """List active sync proxy sessions for monitoring."""
    try:
        from workflows.sync_proxy_manager import get_sync_proxy_manager
        manager = get_sync_proxy_manager()
        if manager:
            sessions = manager.get_sessions_summary()
            active_count = manager.get_active_count()
        else:
            sessions = []
            active_count = 0

        # Also count recent sync jobs from DB for cross-process monitoring
        recent_sync_jobs = CredentialProxyJob.query.filter(
            CredentialProxyJob.proxy_mode == 'sync'
        ).order_by(CredentialProxyJob.created_at.desc()).limit(20).all()

        return jsonify({
            'active_count': active_count,
            'sessions': sessions,
            'recent_sync_jobs': [j.to_dict() for j in recent_sync_jobs],
        })
    except Exception as e:
        logger.exception("Error listing sync proxy sessions: %s", e)
        return jsonify({'error': str(e)}), 500


# Health check
@api_bp.route('/health', methods=['GET'])

def health_check():
    """
    API health check
    ---
    tags:
      - Health
    responses:
      200:
        description: API is healthy
        schema:
          type: object
          properties:
            status:
              type: string
              example: ok
            service:
              type: string
              example: api
            version:
              type: string
              example: 2.0.0
    """
    return jsonify({
        'status': 'ok',
        'service': 'api',
        'version': '2.0.0'
    })

# Error handlers
@api_bp.errorhandler(ValidationException)
def handle_validation_error(e):
    """Handle validation errors"""
    return jsonify({
        'error': 'ValidationError',
        'message': e.message,
        'details': e.errors
    }), 400

@api_bp.route('/campaigns/<int:campaign_id>', methods=['DELETE'])
@require_auth
def delete_campaign(campaign_id):
    """
    Delete a campaign and cleanup associated resources
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
    responses:
      204:
        description: Campaign deleted successfully
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        
        campaign = Campaign.query.get_or_404(campaign_id)
        
        # Check permissions
        current_user = get_current_user()
        if not current_user.is_admin and campaign.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        # Mark non-active up-front so _build_servers_from_db excludes it
        # during the Caddy rebuild below. The final soft-delete commit happens
        # via campaign_service.delete_campaign().
        campaign.status = 'deleted'

        # Remove Caddy configuration if campaign was active
        if campaign.caddy_config_id:
            try:
                caddy_manager.remove_campaign_config(campaign.uid)
                logger.info(f"Removed Caddy configuration for campaign {campaign.uid}")
            except Exception as e:
                logger.warning(f"Failed to remove Caddy config for campaign {campaign.uid}: {e}")

        # Cleanup SSL files and campaign directory
        try:
            caddy_manager.cleanup_campaign_files(campaign.uid)
            logger.info(f"Cleaned up files for campaign {campaign.uid}")
        except Exception as e:
            logger.warning(f"Failed to cleanup files for campaign {campaign.uid}: {e}")

        campaign_name = campaign.name
        campaign_uid = campaign.uid

        # Soft delete: sets status='deleted' and commits. Preserves child rows
        # (events, call_jobs, sms_jobs, credential_proxy_jobs, etc.) for audit.
        campaign_service.delete_campaign(campaign)
        
        logger.info(f"Campaign '{campaign_name}' ({campaign_uid}) deleted by user {current_user.username}")
        
        return jsonify({
            'success': True,
            'message': f'Campaign "{campaign_name}" deleted successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error deleting campaign {campaign_id}")
        return jsonify({
            'success': False,
            'error': f'Error deleting campaign: {str(e)}'
        }), 500

@api_bp.route('/campaigns/<int:campaign_id>/ssl/details', methods=['GET'])

@require_auth
def get_campaign_ssl_details(campaign_id):
    """
    Get SSL certificate details for a campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
    responses:
      200:
        description: SSL certificate details
        schema:
          type: object
          properties:
            success:
              type: boolean
            ssl_info:
              $ref: '#/definitions/SSLDetails'
            error:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        
        campaign = Campaign.query.get_or_404(campaign_id)
        
        # Check permissions
        current_user = get_current_user()
        if not current_user.is_admin and campaign.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        if campaign.ssl_mode != 'custom' or not campaign.ssl_cert_path:
            return jsonify({
                'success': False,
                'error': 'No custom SSL certificate configured for this campaign'
            })
        
        # Read and validate the certificate file
        try:
            with open(campaign.ssl_cert_path, 'rb') as cert_file:
                cert_data = cert_file.read()
            
            with open(campaign.ssl_key_path, 'rb') as key_file:
                key_data = key_file.read()
            
            # Validate certificate and get details
            ssl_info = caddy_manager.validate_ssl_certificate(
                cert_data, key_data, campaign.custom_domain
            )
            
            return jsonify({
                'success': True,
                'ssl_info': ssl_info
            })
            
        except FileNotFoundError:
            return jsonify({
                'success': False,
                'error': 'SSL certificate files not found'
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Error reading certificate: {str(e)}'
            })
        
    except Exception as e:
        logger.exception(f"Error getting SSL details for campaign {campaign_id}")
        return jsonify({
            'success': False,
            'error': f'Error retrieving SSL details: {str(e)}'
        }), 500

@api_bp.route('/caddy/status', methods=['GET'])

@require_auth
def get_caddy_status():
    """
    Get Caddy server status
    ---
    tags:
      - Caddy
    security:
      - Bearer: []
    responses:
      200:
        description: Caddy server status
        schema:
          type: object
          properties:
            running:
              type: boolean
            config:
              type: object
            error:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        status = caddy_manager.get_caddy_status()
        return jsonify(status)
    except Exception as e:
        logger.exception("Error getting Caddy status")
        return jsonify({
            'running': False,
            'config': None,
            'error': str(e)
        })

@api_bp.route('/caddy/config', methods=['GET'])

@require_auth
def get_caddy_config():
    """
    Get current Caddy configuration
    ---
    tags:
      - Caddy
    security:
      - Bearer: []
    responses:
      200:
        description: Current Caddy configuration
        schema:
          type: object
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Admin privileges required
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        current_user = get_current_user()
        
        # Only allow admin users
        if not current_user.is_admin:
            return jsonify({'error': 'Access denied. Admin privileges required.'}), 403
        
        result = caddy_manager.get_current_config()
        if result['success']:
            return jsonify(result['config']), 200
        else:
            return jsonify({'error': result['error']}), 500
    except Exception as e:
        logger.exception("Error getting Caddy config")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/caddy/config', methods=['PUT'])
@require_auth
def update_caddy_config():
    """
    Update Caddy configuration
    ---
    tags:
      - Caddy
    security:
      - Bearer: []
    parameters:
      - in: body
        name: config
        required: true
        schema:
          type: object
          description: Caddy configuration JSON
    responses:
      200:
        description: Configuration updated successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      400:
        description: Invalid JSON or configuration
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Admin privileges required
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        import json
        current_user = get_current_user()
        
        # Only allow admin users
        if not current_user.is_admin:
            return jsonify({'error': 'Access denied. Admin privileges required.'}), 403
        
        # Get config from request body
        config_text = request.get_data(as_text=True)
        if not config_text:
            return jsonify({'error': 'Configuration is required'}), 400
        
        # Validate JSON
        try:
            config_dict = json.loads(config_text)
        except json.JSONDecodeError as e:
            return jsonify({'error': f'Invalid JSON: {str(e)}'}), 400
        
        # Update config
        result = caddy_manager.update_config(config_dict)
        if result['success']:
            logger.info(f"Caddy config updated by user {current_user.username}")
            return jsonify({'success': True, 'message': 'Configuration updated successfully'}), 200
        else:
            return jsonify({'success': False, 'error': result['error']}), 500
    except Exception as e:
        logger.exception("Error updating Caddy config")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/caddy/panic', methods=['POST'])
@require_auth
def enable_panic_mode():
    """
    Enable panic mode - redirect all traffic to microsoft.com
    ---
    tags:
      - Caddy
    security:
      - Bearer: []
    responses:
      200:
        description: Panic mode enabled successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Admin privileges required
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        current_user = get_current_user()
        
        # Only allow admin users
        if not current_user.is_admin:
            return jsonify({'error': 'Access denied. Admin privileges required.'}), 403
        
        result = caddy_manager.enable_panic_mode()
        if result['success']:
            logger.warning(f"PANIC MODE ENABLED by user {current_user.username}")
            return jsonify({'success': True, 'message': 'Panic mode enabled'}), 200
        else:
            return jsonify({'success': False, 'error': result['error']}), 500
    except Exception as e:
        logger.exception("Error enabling panic mode")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/caddy/panic/disable', methods=['POST'])
@require_auth
def disable_panic_mode():
    """
    Disable panic mode - restore original configuration
    ---
    tags:
      - Caddy
    security:
      - Bearer: []
    responses:
      200:
        description: Panic mode disabled successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Admin privileges required
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        current_user = get_current_user()
        
        # Only allow admin users
        if not current_user.is_admin:
            return jsonify({'error': 'Access denied. Admin privileges required.'}), 403
        
        result = caddy_manager.disable_panic_mode()
        if result['success']:
            logger.info(f"Panic mode disabled by user {current_user.username}")
            return jsonify({'success': True, 'message': 'Panic mode disabled'}), 200
        else:
            return jsonify({'success': False, 'error': result['error']}), 500
    except Exception as e:
        logger.exception("Error disabling panic mode")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/caddy/panic/status', methods=['GET'])

@require_auth
def get_panic_status():
    """
    Get panic mode status
    ---
    tags:
      - Caddy
    security:
      - Bearer: []
    responses:
      200:
        description: Panic mode status
        schema:
          type: object
          properties:
            enabled:
              type: boolean
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Admin privileges required
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        current_user = get_current_user()
        
        # Only allow admin users
        if not current_user.is_admin:
            return jsonify({'error': 'Access denied. Admin privileges required.'}), 403
        
        status = caddy_manager.get_panic_status()
        return jsonify(status), 200
    except Exception as e:
        logger.exception("Error getting panic status")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/campaigns/<int:campaign_id>/ssl/test', methods=['POST'])
@require_auth
def test_campaign_ssl_endpoint(campaign_id):
    """
    Test SSL endpoint accessibility for a campaign
    ---
    tags:
      - Campaigns
    security:
      - Bearer: []
    parameters:
      - name: campaign_id
        in: path
        type: integer
        required: true
        description: Campaign ID
      - in: body
        name: test_config
        schema:
          type: object
          properties:
            domain:
              type: string
              description: Domain to test (defaults to campaign domain)
            port:
              type: integer
              default: 443
              description: Port to test
    responses:
      200:
        description: SSL endpoint test results
        schema:
          type: object
          properties:
            accessible:
              type: boolean
            certificate_valid:
              type: boolean
            error:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        
        campaign = Campaign.query.get_or_404(campaign_id)
        
        # Check permissions
        current_user = get_current_user()
        if not current_user.is_admin and campaign.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        data = request.get_json()
        domain = data.get('domain') or campaign.custom_domain
        port = data.get('port', 443 if campaign.ssl_mode != 'disabled' else 80)
        
        if not domain:
            return jsonify({
                'accessible': False,
                'certificate_valid': False,
                'error': 'No domain specified for testing'
            })
        
        # Test the SSL endpoint
        test_result = caddy_manager.test_ssl_endpoint(domain, port)
        
        return jsonify(test_result)
        
    except Exception as e:
        logger.exception(f"Error testing SSL endpoint for campaign {campaign_id}")
        return jsonify({
            'accessible': False,
            'certificate_valid': False,
            'error': f'Error testing endpoint: {str(e)}'
        }), 500

# Template Assets endpoints
@api_bp.route('/templates/<int:template_id>/assets', methods=['GET'])

@require_auth
def list_template_assets(template_id):
    """
    List assets for a template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
    responses:
      200:
        description: List of template assets
        schema:
          type: object
          properties:
            template_id:
              type: integer
            template_name:
              type: string
            assets:
              type: array
              items:
                $ref: '#/definitions/Asset'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        
        template = Template.query.get_or_404(template_id)
        
        # Check permissions
        current_user = get_current_user()
        if not current_user.is_admin and template.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        assets = caddy_manager.list_template_assets(template_id)
        
        return jsonify({
            'template_id': template_id,
            'template_name': template.name,
            'assets': assets
        })
        
    except Exception as e:
        logger.exception(f"Error listing assets for template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/assets', methods=['POST'])
@require_auth
def upload_template_asset(template_id):
    """
    Upload an asset for a template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
      - name: file
        in: formData
        type: file
        required: true
        description: Asset file to upload (max 10MB)
    responses:
      201:
        description: Asset uploaded successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            filename:
              type: string
            url:
              type: string
            size:
              type: integer
      400:
        description: Validation error or file too large
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        
        template = Template.query.get_or_404(template_id)
        
        # Check permissions
        current_user = get_current_user()
        if not current_user.is_admin and template.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Check file size
        file_data = file.read()
        max_size = 10 * 1024 * 1024  # 10MB
        if len(file_data) > max_size:
            return jsonify({'error': 'File too large (max 10MB)'}), 400
        
        # Upload file
        asset_url = caddy_manager.upload_template_asset(
            template_id, file.filename, file_data
        )
        
        return jsonify({
            'success': True,
            'filename': file.filename,
            'url': asset_url,
            'size': len(file_data)
        }), 201
        
    except Exception as e:
        logger.exception(f"Error uploading asset for template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/assets/<filename>', methods=['DELETE'])
@require_auth
def delete_template_asset(template_id, filename):
    """
    Delete an asset for a template
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
      - name: filename
        in: path
        type: string
        required: true
        description: Asset filename
    responses:
      200:
        description: Asset deleted successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template or asset not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.caddy import caddy_manager
        
        template = Template.query.get_or_404(template_id)
        
        # Check permissions
        current_user = get_current_user()
        if not current_user.is_admin and template.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        success = caddy_manager.delete_template_asset(template_id, filename)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Asset {filename} deleted successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Asset not found'
            }), 404
        
    except Exception as e:
        logger.exception(f"Error deleting asset {filename} for template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/assets/<path:filename>', methods=['GET'])
@require_auth
def get_template_asset(template_id, filename):
    """Serve a single template asset file (for preview and direct access)."""
    try:
        from shared.caddy import caddy_manager
        import mimetypes

        template = Template.query.get_or_404(template_id)
        current_user = get_current_user()
        if not template.is_public and template.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        if '..' in filename or filename.startswith('/'):
            return jsonify({'error': 'Invalid path'}), 400
        assets_dir = caddy_manager.get_template_assets_dir(template_id)
        file_path = (assets_dir / filename).resolve()
        if not file_path.is_file() or not str(file_path).startswith(str(assets_dir.resolve())):
            return jsonify({'error': 'Not found'}), 404
        mimetype, _ = mimetypes.guess_type(filename)
        return send_file(str(file_path), mimetype=mimetype or 'application/octet-stream', max_age=0)
    except Exception as e:
        logger.exception(f"Error serving asset {filename} for template {template_id}")
        return jsonify({'error': str(e)}), 500

# Settings endpoints
@api_bp.route('/settings', methods=['GET'])

@require_auth
def get_settings():
    """
    Get all settings organized by category
    ---
    tags:
      - Settings
    security:
      - Bearer: []
    responses:
      200:
        description: Settings organized by category
        schema:
          type: object
          properties:
            success:
              type: boolean
            categories:
              type: object
              additionalProperties:
                type: array
                items:
                  $ref: '#/definitions/Setting'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.config import Config
        
        # Get all settings from database
        settings = Setting.query.all()
        settings_dict = {s.key: s.to_dict() for s in settings}
        
        # Organize by category
        categories = {}
        for key, meta in Config._db_manageable_settings.items():
            category = meta.get('category', 'general')
            if category not in categories:
                categories[category] = []
            
            # Get current value (database or default)
            current_value = Config.get_setting(key, meta['default'])
            
            setting_info = {
                'key': key,
                'value': current_value if not meta.get('is_sensitive', False) else ('***' if current_value else ''),
                'default': meta['default'],
                'type': meta['type'],
                'description': meta.get('description', ''),
                'is_sensitive': meta.get('is_sensitive', False),
                'in_database': key in settings_dict,
                'updated_at': settings_dict[key]['updated_at'] if key in settings_dict else None
            }
            categories[category].append(setting_info)
        
        return jsonify({
            'success': True,
            'categories': categories
        })
        
    except Exception as e:
        logger.exception("Error getting settings")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/settings/<key>', methods=['PUT'])
@require_auth
def update_setting(key):
    """
    Update a specific setting
    ---
    tags:
      - Settings
    security:
      - Bearer: []
    parameters:
      - name: key
        in: path
        type: string
        required: true
        description: Setting key
      - in: body
        name: setting
        required: true
        schema:
          type: object
          required:
            - value
          properties:
            value:
              type: string
              description: Setting value
    responses:
      200:
        description: Setting updated successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
            value:
              type: string
      400:
        description: Validation error
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.config import Config
        
        if key not in Config._db_manageable_settings:
            return jsonify({'error': f'Setting {key} is not configurable'}), 400
        
        data = request.get_json()
        if not data or 'value' not in data:
            return jsonify({'error': 'Value is required'}), 400
        
        value = data['value']
        setting_meta = Config._db_manageable_settings[key]
        
        # Type validation
        try:
            if setting_meta['type'] == 'int':
                value = int(value)
            elif setting_meta['type'] == 'bool':
                value = bool(value) if isinstance(value, bool) else str(value).lower() in ('true', '1', 'yes', 'on')
            else:
                value = str(value)
        except (ValueError, TypeError):
            return jsonify({'error': f'Invalid {setting_meta["type"]} value'}), 400
        
        # Update or create setting
        Setting.set_setting(
            key=key,
            value=value,
            value_type=setting_meta['type'],
            category=setting_meta.get('category'),
            description=setting_meta.get('description'),
            is_sensitive=setting_meta.get('is_sensitive', False)
        )
        
        return jsonify({
            'success': True,
            'message': f'Setting {key} updated successfully',
            'value': value if not setting_meta.get('is_sensitive', False) else '***'
        })
        
    except Exception as e:
        logger.exception(f"Error updating setting {key}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/settings/<key>', methods=['DELETE'])
@require_auth 
def delete_setting(key):
    """
    Delete a setting (revert to .env/default)
    ---
    tags:
      - Settings
    security:
      - Bearer: []
    parameters:
      - name: key
        in: path
        type: string
        required: true
        description: Setting key
    responses:
      200:
        description: Setting deleted successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Setting not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        setting = Setting.query.filter_by(key=key).first()
        if not setting:
            return jsonify({'error': 'Setting not found'}), 404
        
        db.session.delete(setting)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Setting {key} deleted (reverted to default)'
        })
        
    except Exception as e:
        logger.exception(f"Error deleting setting {key}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/settings/reset', methods=['POST'])
@require_auth
def reset_all_settings():
    """
    Reset all settings to defaults
    ---
    tags:
      - Settings
    security:
      - Bearer: []
    responses:
      200:
        description: All settings reset to defaults
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        Setting.query.delete()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'All settings reset to defaults'
        })
        
    except Exception as e:
        logger.exception("Error resetting settings")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/settings/import', methods=['POST'])
@require_auth
def import_env_settings():
    """
    Import current .env values to database
    ---
    tags:
      - Settings
    security:
      - Bearer: []
    responses:
      200:
        description: Settings imported successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
            imported_count:
              type: integer
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        import os
        from shared.config import Config
        
        imported = 0
        for key, meta in Config._db_manageable_settings.items():
            env_value = os.getenv(key)
            if env_value:
                # Type conversion
                if meta['type'] == 'int':
                    try:
                        env_value = int(env_value)
                    except ValueError:
                        continue
                elif meta['type'] == 'bool':
                    env_value = env_value.lower() in ('true', '1', 'yes', 'on')
                
                Setting.set_setting(
                    key=key,
                    value=env_value,
                    value_type=meta['type'],
                    category=meta.get('category'),
                    description=meta.get('description'),
                    is_sensitive=meta.get('is_sensitive', False)
                )
                imported += 1
        
        return jsonify({
            'success': True,
            'message': f'Imported {imported} settings from .env file'
        })
        
    except Exception as e:
        logger.exception("Error importing .env settings")
        return jsonify({'error': str(e)}), 500

# Template Package endpoints
@api_bp.route('/templates/preview-package', methods=['POST'])
@require_auth
def preview_template_package():
    """Preview contents of a template zip package without importing."""
    try:
        from shared.template_package import TemplatePackageHandler
        from werkzeug.utils import secure_filename
        import uuid

        if 'package' not in request.files and 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        file = request.files.get('package') or request.files.get('file')
        if not file or file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        if not file.filename.lower().endswith('.zip'):
            return jsonify({'error': 'File must be a .zip archive'}), 400

        handler = TemplatePackageHandler()
        temp_filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
        temp_path = handler.temp_dir / temp_filename
        try:
            file.save(str(temp_path))
            result = handler.list_package_contents(temp_path)
            if isinstance(result, dict) and 'error' in result:
                return jsonify({'error': result['error']}), 400
            return jsonify(result)
        finally:
            if temp_path.exists():
                temp_path.unlink()
    except Exception as e:
        logger.exception("Error previewing template package")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/import', methods=['POST'])
@require_auth
def import_template_package():
    """
    Import a template from a zip package
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: file
        in: formData
        type: file
        required: true
        description: ZIP file containing template package
    responses:
      200:
        description: Template imported successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            messages:
              type: array
              items:
                type: string
            template_id:
              type: integer
            errors:
              type: array
              items:
                type: string
      400:
        description: Validation error or import failed
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.template_package import TemplatePackageHandler
        from werkzeug.utils import secure_filename
        from pathlib import Path
        import uuid
        
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.lower().endswith('.zip'):
            return jsonify({'error': 'File must be a .zip archive'}), 400
        
        # Save uploaded file temporarily
        handler = TemplatePackageHandler()
        temp_filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
        temp_path = handler.temp_dir / temp_filename
        
        try:
            file.save(str(temp_path))
            
            # Get current user
            current_user_obj = get_current_user()
            user_id = current_user_obj.id if current_user_obj else None
            
            # Import the package
            success, messages, template_id = handler.import_package(temp_path, user_id)

            if success:
                # Score the freshly imported template(s) — the import path bypasses
                # TemplateService.create_template, so detection wouldn't otherwise run.
                imported_ids = template_id if isinstance(template_id, list) else (
                    [template_id] if template_id else []
                )
                for tid in imported_ids:
                    imported = Template.query.get(tid)
                    if imported:
                        template_service._run_phishing_detection_template(imported)
                if imported_ids:
                    try:
                        db.session.commit()
                    except Exception as e:
                        logger.warning(
                            f"Failed to persist phishing detection for imported templates {imported_ids}: {e}"
                        )
                        db.session.rollback()

                return jsonify({
                    'success': True,
                    'messages': messages,
                    'template_id': template_id
                })
            else:
                return jsonify({
                    'success': False,
                    'errors': messages,
                    'error': messages[0] if messages else 'Import failed'
                }), 400
        
        finally:
            # Clean up temp file
            if temp_path.exists():
                temp_path.unlink()
    
    except Exception as e:
        logger.exception("Error importing template package")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/<int:template_id>/export', methods=['GET', 'POST'])
@require_auth
def export_template_package(template_id):
    """
    Export a template as a zip package
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
    responses:
      200:
        description: Template package file
        schema:
          type: file
      400:
        description: Export failed
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Template not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.template_package import TemplatePackageHandler
        from flask import send_file
        
        handler = TemplatePackageHandler()
        success, message, zip_path = handler.export_template(template_id)
        
        if not success:
            return jsonify({'error': message}), 400
        
        if not zip_path or not zip_path.exists():
            return jsonify({'error': 'Export file not found'}), 500
        
        # Send file and clean up after
        def remove_file(response):
            try:
                zip_path.unlink()
            except Exception:
                pass
            return response
        
        return send_file(
            str(zip_path),
            as_attachment=True,
            download_name=zip_path.name,
            mimetype='application/zip'
        )
    
    except Exception as e:
        logger.exception(f"Error exporting template {template_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/validate', methods=['POST'])
@require_auth
def validate_template_package():
    """Validate a template package without importing"""
    try:
        from shared.template_package import TemplatePackageHandler
        from werkzeug.utils import secure_filename
        import uuid
        
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '' or not file.filename.lower().endswith('.zip'):
            return jsonify({'error': 'Invalid file type'}), 400
        
        # Save uploaded file temporarily
        handler = TemplatePackageHandler()
        temp_filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
        temp_path = handler.temp_dir / temp_filename
        
        try:
            file.save(str(temp_path))
            
            # Validate the package
            is_valid, errors, metadata = handler.validate_package(temp_path)
            
            # Get package contents for preview
            contents = handler.list_package_contents(temp_path)
            
            return jsonify({
                'valid': is_valid,
                'errors': errors,
                'metadata': metadata,
                'contents': contents
            })
        
        finally:
            # Clean up temp file
            if temp_path.exists():
                temp_path.unlink()
    
    except Exception as e:
        logger.exception("Error validating template package")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/templates/packages/cleanup', methods=['POST'])
@require_auth
def cleanup_template_packages():
    """
    Clean up temporary template package files
    ---
    tags:
      - Templates
    security:
      - Bearer: []
    parameters:
      - in: body
        name: cleanup_config
        schema:
          type: object
          properties:
            hours:
              type: integer
              default: 24
              description: Delete files older than this many hours
    responses:
      200:
        description: Cleanup completed
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.template_package import TemplatePackageHandler
        
        hours = request.json.get('hours', 24) if request.json else 24
        
        handler = TemplatePackageHandler()
        handler.cleanup_temp_files(hours)
        
        return jsonify({
            'success': True,
            'message': f'Cleaned up temporary files older than {hours} hours'
        })
    
    except Exception as e:
        logger.exception("Error during cleanup")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/sending-workflows', methods=['GET'])
@require_auth
def list_sending_workflows():
    """List sending workflows with optional filters."""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        per_page = min(per_page, 100)
        status = request.args.get('status')
        campaign_id = request.args.get('campaign_id', type=int)
        user_id = request.args.get('user_id', type=int)
        search = request.args.get('search')
        result = sending_workflow_service.list_sending_workflows(
            page=page, per_page=per_page, status=status,
            campaign_id=campaign_id, user_id=user_id, search=search
        )
        return jsonify(result)
    except Exception as e:
        logger.exception("Error listing sending workflows")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/sending-workflows', methods=['POST'])
@require_auth
def create_sending_workflow():
    """Create a new sending workflow (workflow_id from builder required)."""
    try:
        data = request.get_json() or {}
        name = data.get('name')
        campaign_id = data.get('campaign_id')
        target_selection_config = data.get('target_selection_config') or {}
        sending_method_config = data.get('sending_method_config') or {}
        if not name:
            return jsonify({'error': 'name is required'}), 400
        workflow_id = data.get('workflow_id')
        if workflow_id is None:
            return jsonify({'error': 'workflow_id is required'}), 400
        description = data.get('description', '')
        template_validation_config = data.get('template_validation_config')
        pre_render_config = data.get('pre_render_config')
        workflow = sending_workflow_service.create_sending_workflow(
            name=name,
            campaign_id=campaign_id,
            target_selection_config=target_selection_config,
            sending_method_config=sending_method_config,
            description=description,
            template_validation_config=template_validation_config,
            pre_render_config=pre_render_config,
            workflow_id=workflow_id,
            user=get_current_user()
        )
        return jsonify(workflow.to_dict()), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception("Error creating sending workflow")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/sending-workflows/<int:workflow_id>', methods=['GET'])
@require_auth
def get_sending_workflow(workflow_id):
    """Get a single sending workflow (includes pre_render_config via to_dict)."""
    try:
        workflow = sending_workflow_service.get_sending_workflow(workflow_id)
        if not workflow:
            return jsonify({'error': 'Sending workflow not found'}), 404
        current_user = get_current_user()
        if workflow.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        return jsonify(workflow.to_dict())
    except Exception as e:
        logger.exception(f"Error getting sending workflow {workflow_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/sending-workflows/<int:workflow_id>', methods=['PUT'])
@require_auth
def update_sending_workflow(workflow_id):
    """Update a sending workflow (accepts pre_render_config in body)."""
    try:
        workflow = sending_workflow_service.get_sending_workflow(workflow_id)
        if not workflow:
            return jsonify({'error': 'Sending workflow not found'}), 404
        current_user = get_current_user()
        if workflow.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        data = request.get_json() or {}
        updates = {}
        for key in ['name', 'description', 'campaign_id', 'workflow_id', 'target_selection_config',
                    'sending_method_config', 'template_validation_config', 'pre_render_config', 'status', 'scheduled_at']:
            if key in data:
                updates[key] = data[key]
        if not updates:
            return jsonify(workflow.to_dict())
        updated = sending_workflow_service.update_sending_workflow(workflow, updates)
        return jsonify(updated.to_dict())
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f"Error updating sending workflow {workflow_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/sending-workflows/<int:workflow_id>', methods=['DELETE'])
@require_auth
def delete_sending_workflow(workflow_id):
    """Delete a sending workflow."""
    try:
        workflow = sending_workflow_service.get_sending_workflow(workflow_id)
        if not workflow:
            return jsonify({'error': 'Sending workflow not found'}), 404
        current_user = get_current_user()
        if workflow.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        sending_workflow_service.delete_sending_workflow(workflow)
        return jsonify({'success': True}), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f"Error deleting sending workflow {workflow_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/sending-workflows/csv-files', methods=['GET'])
@require_auth
def list_sending_workflow_csv_files():
    """List CSV files under storage/uploads (for target selector file dropdown)."""
    try:
        from pathlib import Path
        base = Path.cwd().resolve()
        storage = base / "storage" / "uploads"
        targets_dir = storage / "targets"
        files = []
        for directory in [targets_dir, storage]:
            if not directory.exists() or not directory.is_dir():
                continue
            try:
                for f in sorted(directory.iterdir()):
                    if f.is_file() and f.suffix.lower() == ".csv":
                        try:
                            rel = f.relative_to(base)
                            files.append(str(rel).replace("\\", "/"))
                        except ValueError:
                            pass
            except Exception as e:
                logger.warning(f"Error listing {directory}: {e}")
        return jsonify({"files": files})
    except Exception as e:
        logger.exception("Error listing CSV files")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/sending-workflows/<int:workflow_id>/preview', methods=['POST'])
@require_auth
def preview_sending_workflow_email(workflow_id):
    """
    Preview rendered email for a sending workflow
    ---
    tags:
      - Sending Workflows
    security:
      - Bearer: []
    parameters:
      - name: workflow_id
        in: path
        type: integer
        required: true
        description: Sending workflow ID
      - in: body
        name: preview_config
        schema:
          type: object
          properties:
            sample_email:
              type: string
              format: email
              default: test@example.com
            sample_data:
              type: object
              description: Sample data for email rendering
    responses:
      200:
        description: Rendered email preview
        schema:
          type: object
          properties:
            subject:
              type: string
            html:
              type: string
            text:
              type: string
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Workflow or campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        workflow = sending_workflow_service.get_sending_workflow(workflow_id)
        if not workflow:
            return jsonify({'error': 'Sending workflow not found'}), 404
        
        # Check permissions
        current_user = get_current_user()
        if workflow.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        data = request.get_json() or {}
        sample_email = data.get('sample_email', 'test@example.com')
        sample_data = data.get('sample_data', {})
        
        from shared.database import Campaign
        campaign = workflow.campaign
        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404
        
        # Create sample target
        target = {
            'email': sample_email,
            'first_name': sample_data.get('first_name', 'John'),
            'last_name': sample_data.get('last_name', 'Doe'),
            'name': sample_data.get('name', 'John Doe'),
            'custom_data': sample_data.get('custom_data', {})
        }
        
        # Render email using singleton executor (with optional pre-render)
        from workflows.sending_executor import get_executor
        executor = get_executor()
        extra_context = executor._run_pre_render(workflow, campaign, target)
        rendered_email = executor._render_email(campaign, target, extra_context=extra_context)
        
        return jsonify({
            'subject': rendered_email.get('subject'),
            'html': rendered_email.get('html'),
            'text': rendered_email.get('text')
        })
    except Exception as e:
        logger.exception(f"Error previewing email for workflow {workflow_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/sending-workflows/<int:workflow_id>/test-email', methods=['POST'])
@require_auth
def send_test_email(workflow_id):
    """
    Send a test email for a sending workflow
    ---
    tags:
      - Sending Workflows
    security:
      - Bearer: []
    parameters:
      - name: workflow_id
        in: path
        type: integer
        required: true
        description: Sending workflow ID
      - in: body
        name: test_config
        required: true
        schema:
          type: object
          required:
            - test_email
          properties:
            test_email:
              type: string
              format: email
              description: Email address to send test email to
    responses:
      200:
        description: Test email sent successfully
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [sent, failed]
            message:
              type: string
            error:
              type: string
      400:
        description: Validation error or send failed
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Workflow or campaign not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        workflow = sending_workflow_service.get_sending_workflow(workflow_id)
        if not workflow:
            return jsonify({'error': 'Sending workflow not found'}), 404
        
        # Check permissions
        current_user = get_current_user()
        if workflow.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        data = request.get_json() or {}
        test_email = data.get('test_email')
        if not test_email:
            return jsonify({'error': 'test_email is required'}), 400
        
        from shared.database import Campaign
        campaign = workflow.campaign
        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404
        
        # Create test target
        target = {
            'email': test_email,
            'first_name': 'Test',
            'last_name': 'User',
            'name': 'Test User',
            'custom_data': {}
        }
        
        # Render and send email using singleton executor (with optional pre-render)
        from workflows.sending_executor import get_executor
        executor = get_executor()
        extra_context = executor._run_pre_render(workflow, campaign, target)
        rendered_email = executor._render_email(campaign, target, extra_context=extra_context)
        
        # Send test email via plugin
        send_result = executor._send_email(workflow, target, rendered_email)
        
        if send_result.get('success', False):
            return jsonify({
                'status': 'sent',
                'message': f'Test email sent successfully to {test_email}'
            })
        else:
            return jsonify({
                'status': 'failed',
                'error': send_result.get('error', 'Unknown error')
            }), 400
    except Exception as e:
        logger.exception(f"Error sending test email for workflow {workflow_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/sending-workflows/<int:workflow_id>/execute', methods=['POST'])
@require_auth
def execute_sending_workflow_route(workflow_id):
    """
    Execute a sending workflow (run email send).
    """
    try:
        workflow = sending_workflow_service.get_sending_workflow(workflow_id)
        if not workflow:
            return jsonify({'error': 'Sending workflow not found'}), 404

        current_user = get_current_user()
        if workflow.created_by_id != current_user.id and not current_user.is_admin:
            return jsonify({'error': 'Access denied'}), 403

        # Refuse to send if the linked campaign is not active
        if workflow.campaign_id:
            from shared.database import Campaign
            campaign = Campaign.query.get(workflow.campaign_id)
            if campaign and campaign.status != 'active':
                return jsonify({
                    'error': f"Campaign is {campaign.status}. Activate it before sending."
                }), 409

        from workflows.sending_executor import execute_sending_workflow
        result = execute_sending_workflow(workflow_id, dry_run=False)
        return jsonify(result or {'status': 'completed'}), 200
    except ValueError as e:
        msg = str(e)
        if 'already running' in msg.lower():
            return jsonify({'error': msg}), 409
        return jsonify({'error': msg}), 400
    except Exception as e:
        logger.exception(f"Error executing sending workflow {workflow_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/plugins', methods=['GET'])

@require_auth
def list_plugins():
    """
    List all plugins
    ---
    tags:
      - Plugins
    security:
      - Bearer: []
    parameters:
      - name: category
        in: query
        type: string
        description: Filter by plugin category
      - name: active_only
        in: query
        type: boolean
        default: true
        description: Show only active plugins
    responses:
      200:
        description: List of plugins
        schema:
          type: object
          properties:
            plugins:
              type: array
              items:
                $ref: '#/definitions/Plugin'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        category = request.args.get('category')
        active_only = request.args.get('active_only', 'true').lower() == 'true'
        
        plugins = plugin_service.list_plugins(category=category, active_only=active_only)
        return jsonify({'plugins': plugins})
        
    except Exception as e:
        logger.exception("Error listing plugins")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/plugins', methods=['POST'])
@require_auth
def upload_plugin():
    """
    Upload and register a new plugin
    ---
    tags:
      - Plugins
    security:
      - Bearer: []
    parameters:
      - name: file
        in: formData
        type: file
        required: true
        description: Python plugin file (max 1MB)
    responses:
      201:
        description: Plugin uploaded and registered successfully
        schema:
          $ref: '#/definitions/Plugin'
      400:
        description: Validation error or file too large
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Admin privileges required
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from pathlib import Path
        from shared.config import Config
        import os
        import shutil
        
        current_user = get_current_user()
        
        # Check if user is admin (optional - can be removed if all users should upload)
        if not current_user.is_admin:
            return jsonify({'error': 'Only administrators can upload plugins'}), 403
        
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Validate file extension
        if not file.filename.endswith('.py'):
            return jsonify({'error': 'Only Python (.py) files are allowed'}), 400
        
        # Check file size (max 1MB for plugin files)
        file_data = file.read()
        max_size = 1024 * 1024  # 1MB
        if len(file_data) > max_size:
            return jsonify({'error': 'File too large (max 1MB)'}), 400
        
        # Create plugins directory if it doesn't exist
        plugins_dir = Path('storage/plugins')
        plugins_dir.mkdir(parents=True, exist_ok=True)
        
        # Sanitize filename
        safe_filename = "".join(c for c in file.filename if c.isalnum() or c in ('-', '_', '.'))
        if not safe_filename.endswith('.py'):
            safe_filename = safe_filename + '.py'
        
        # Save file
        file_path = plugins_dir / safe_filename
        # If file exists, add timestamp
        if file_path.exists():
            import time
            timestamp = int(time.time())
            name_parts = safe_filename.rsplit('.', 1)
            safe_filename = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
            file_path = plugins_dir / safe_filename
        
        file_path.write_bytes(file_data)
        
        # Try to load and validate plugin
        registry = plugin_service.registry
        loaded_plugin = registry.load_plugin_from_path(str(file_path))
        
        if not loaded_plugin:
            # Clean up file if loading failed
            file_path.unlink()
            return jsonify({'error': 'Failed to load plugin. Ensure it contains a class inheriting from BasePlugin'}), 400
        
        # Validate plugin
        validation_errors = registry.validate_plugin(loaded_plugin)
        if validation_errors:
            # Clean up file if validation failed
            file_path.unlink()
            return jsonify({'error': 'Plugin validation failed', 'details': validation_errors}), 400
        
        # Check if plugin_type already exists
        existing_plugin = Plugin.query.filter_by(plugin_type=loaded_plugin.plugin_type).first()
        if existing_plugin:
            # Clean up file if duplicate
            file_path.unlink()
            return jsonify({'error': f'Plugin with type "{loaded_plugin.plugin_type}" already exists'}), 400
        
        # Create database record
        db_plugin = plugin_service.create_custom_plugin(
            name=loaded_plugin.display_name,
            plugin_type=loaded_plugin.plugin_type,
            plugin_category=loaded_plugin.plugin_category,
            description=loaded_plugin.description,
            code_path=str(file_path),
            config_schema=loaded_plugin.config_schema,
            user=current_user
        )
        
        logger.info(f"Plugin uploaded and registered: {loaded_plugin.plugin_type} by {current_user.username}")
        return jsonify(db_plugin.to_dict()), 201
        
    except ValueError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception("Error uploading plugin")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/plugins/<int:plugin_id>/code', methods=['GET'])

@require_auth
def get_plugin_code(plugin_id):
    """
    Get plugin source code
    ---
    tags:
      - Plugins
    security:
      - Bearer: []
    parameters:
      - name: plugin_id
        in: path
        type: integer
        required: true
        description: Plugin ID
    responses:
      200:
        description: Plugin source code
        schema:
          type: file
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Plugin not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from pathlib import Path
        from flask import Response
        import inspect
        from plugins import get_registry
        
        plugin = Plugin.query.get_or_404(plugin_id)
        current_user = get_current_user()
        
        # Check permissions for custom plugins
        if not plugin.is_builtin:
            if not current_user.is_admin and plugin.created_by_id != current_user.id:
                return jsonify({'error': 'Access denied'}), 403
        
        # For built-in plugins, get code from registry
        if plugin.is_builtin:
            registry = get_registry()
            plugin_instance = registry.get_plugin(plugin.plugin_type)
            
            if not plugin_instance:
                return jsonify({'error': 'Plugin not found in registry'}), 404
            
            # Get the file path using inspect
            try:
                plugin_class = type(plugin_instance)
                file_path = Path(inspect.getfile(plugin_class))
                
                if not file_path.exists():
                    return jsonify({'error': 'Plugin source file not found'}), 404
                
                code = file_path.read_text(encoding='utf-8')
                return Response(code, mimetype='text/plain')
            except (TypeError, OSError) as e:
                logger.error(f"Could not get source file for built-in plugin {plugin.plugin_type}: {e}")
                return jsonify({'error': 'Could not locate plugin source file'}), 404
        
        # For custom plugins, read from code_path
        if not plugin.code_path:
            return jsonify({'error': 'Plugin code path not found'}), 404
        
        file_path = Path(plugin.code_path)
        if not file_path.exists():
            return jsonify({'error': 'Plugin file not found'}), 404
        
        code = file_path.read_text(encoding='utf-8')
        
        return Response(code, mimetype='text/plain')
        
    except Exception as e:
        logger.exception(f"Error getting plugin code for {plugin_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/plugins/<int:plugin_id>', methods=['PUT'])
@require_auth
def update_plugin(plugin_id):
    """
    Update plugin code
    ---
    tags:
      - Plugins
    security:
      - Bearer: []
    parameters:
      - name: plugin_id
        in: path
        type: integer
        required: true
        description: Plugin ID
      - in: body
        name: plugin
        required: true
        schema:
          type: object
          required:
            - code
          properties:
            code:
              type: string
              description: Updated plugin source code
    responses:
      200:
        description: Plugin updated successfully
        schema:
          $ref: '#/definitions/Plugin'
      400:
        description: Validation error or cannot edit built-in plugins
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Plugin not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from pathlib import Path
        import ast
        import inspect
        
        plugin = Plugin.query.get_or_404(plugin_id)
        current_user = get_current_user()
        
        # Check permissions
        if not current_user.is_admin and plugin.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        # Only allow editing custom plugins
        if plugin.is_builtin:
            return jsonify({'error': 'Cannot edit built-in plugins'}), 400
        
        if not plugin.code_path:
            return jsonify({'error': 'Plugin code path not found'}), 404
        
        # Get updated code from request
        data = request.get_json()
        if not data or 'code' not in data:
            return jsonify({'error': 'Code is required'}), 400
        
        new_code = data['code']
        
        # Validate code is not empty
        if not new_code or not new_code.strip():
            return jsonify({'error': 'Code cannot be empty'}), 400
        
        # Validate Python syntax
        try:
            ast.parse(new_code)
        except SyntaxError as e:
            return jsonify({'error': f'Invalid Python syntax: {str(e)}'}), 400
        
        # Backup original file
        file_path = Path(plugin.code_path)
        if file_path.exists():
            backup_path = file_path.with_suffix('.py.backup')
            backup_path.write_text(file_path.read_text(encoding='utf-8'), encoding='utf-8')
        
        # Write new code
        file_path.write_text(new_code, encoding='utf-8')
        
        # Try to load and validate plugin
        registry = plugin_service.registry
        
        # Remove old plugin from registry
        if plugin.plugin_type in registry._plugins:
            del registry._plugins[plugin.plugin_type]
        
        # Load new plugin
        loaded_plugin = registry.load_plugin_from_path(str(file_path))
        
        if not loaded_plugin:
            # Restore backup if loading failed
            if backup_path.exists():
                file_path.write_text(backup_path.read_text(encoding='utf-8'), encoding='utf-8')
            return jsonify({'error': 'Failed to load plugin. Ensure it contains a class inheriting from BasePlugin'}), 400
        
        # Validate plugin_type hasn't changed
        if loaded_plugin.plugin_type != plugin.plugin_type:
            # Restore backup if plugin_type changed
            if backup_path.exists():
                file_path.write_text(backup_path.read_text(encoding='utf-8'), encoding='utf-8')
            # Remove the incorrectly loaded plugin
            if loaded_plugin.plugin_type in registry._plugins:
                del registry._plugins[loaded_plugin.plugin_type]
            return jsonify({'error': f'Plugin type cannot be changed. Expected "{plugin.plugin_type}", got "{loaded_plugin.plugin_type}"'}), 400
        
        # Validate plugin
        validation_errors = registry.validate_plugin(loaded_plugin)
        if validation_errors:
            # Restore backup if validation failed
            if backup_path.exists():
                file_path.write_text(backup_path.read_text(encoding='utf-8'), encoding='utf-8')
            # Remove the invalid plugin
            if loaded_plugin.plugin_type in registry._plugins:
                del registry._plugins[loaded_plugin.plugin_type]
            return jsonify({'error': 'Plugin validation failed', 'details': validation_errors}), 400
        
        # Update database record with new metadata
        plugin.name = loaded_plugin.display_name
        plugin.description = loaded_plugin.description
        plugin.plugin_category = loaded_plugin.plugin_category
        plugin.config_schema = loaded_plugin.config_schema
        plugin.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        # Clean up backup file
        if backup_path.exists():
            backup_path.unlink()
        
        logger.info(f"Plugin {plugin.plugin_type} updated by {current_user.username}")
        return jsonify(plugin.to_dict())
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error updating plugin {plugin_id}")
        # Try to restore backup if it exists
        if 'backup_path' in locals() and backup_path.exists():
            try:
                file_path.write_text(backup_path.read_text(encoding='utf-8'), encoding='utf-8')
            except:
                pass
        return jsonify({'error': str(e)}), 500

@api_bp.route('/plugins/<int:plugin_id>', methods=['DELETE'])
@require_auth
def delete_plugin(plugin_id):
    """
    Delete a custom plugin
    ---
    tags:
      - Plugins
    security:
      - Bearer: []
    parameters:
      - name: plugin_id
        in: path
        type: integer
        required: true
        description: Plugin ID
    responses:
      200:
        description: Plugin deleted successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      400:
        description: Cannot delete built-in plugins
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Plugin not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from pathlib import Path
        
        plugin = Plugin.query.get_or_404(plugin_id)
        current_user = get_current_user()
        
        # Check permissions
        if not current_user.is_admin and plugin.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        # Delete plugin
        plugin_service.delete_plugin(plugin)
        
        # Delete plugin file if it exists
        if plugin.code_path:
            file_path = Path(plugin.code_path)
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Deleted plugin file: {plugin.code_path}")
        
        # Remove from registry
        registry = plugin_service.registry
        if plugin.plugin_type in registry._plugins:
            del registry._plugins[plugin.plugin_type]
        
        return jsonify({'success': True, 'message': 'Plugin deleted'}), 200
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f"Error deleting plugin {plugin_id}")
        return jsonify({'error': str(e)}), 500

@api_bp.errorhandler(404)
def handle_not_found(e):
    """Handle 404 errors"""
    return jsonify({
        'error': 'NotFound',
        'message': 'The requested resource was not found'
    }), 404

@api_bp.route('/workflows', methods=['GET'])

@require_auth
def list_workflows():
    """
    List all workflows
    ---
    tags:
      - Workflows
    security:
      - Bearer: []
    parameters:
      - name: type
        in: query
        type: string
        enum: [campaign, sending]
        description: Filter by workflow type
      - name: is_active
        in: query
        type: boolean
        description: Filter by active status
    responses:
      200:
        description: List of workflows
        schema:
          type: object
          properties:
            workflows:
              type: array
              items:
                $ref: '#/definitions/Workflow'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        user = get_current_user()
        workflow_type = request.args.get('type')  # 'campaign' or 'sending'
        is_active = request.args.get('is_active')
        
        query = Workflow.query
        
        # Filter by workflow type if provided
        if workflow_type:
            query = query.filter(Workflow.workflow_type == workflow_type)
        
        # Filter by active status if provided
        if is_active is not None:
            is_active_bool = is_active.lower() == 'true'
            query = query.filter(Workflow.is_active == is_active_bool)
        
        # Filter by user permissions (show public workflows or user's own workflows)
        query = query.filter(
            (Workflow.is_public == True) | (Workflow.created_by_id == user.id)
        )
        
        workflows = query.order_by(Workflow.created_at.desc()).all()
        
        return jsonify({
            'workflows': [workflow.to_dict(include_data=False) for workflow in workflows]
        }), 200
        
    except Exception as e:
        logger.exception(f"Error listing workflows: {e}")
        return jsonify({
            'error': 'InternalServerError',
            'message': 'Failed to list workflows'
        }), 500

@api_bp.route('/workflows', methods=['POST'])
@require_auth
def create_workflow():
    """
    Create a new workflow
    ---
    tags:
      - Workflows
    security:
      - Bearer: []
    parameters:
      - in: body
        name: workflow
        required: true
        schema:
          type: object
          required:
            - name
            - workflow_type
          properties:
            name:
              type: string
              description: Workflow name
            description:
              type: string
              description: Workflow description
            workflow_type:
              type: string
              enum: [campaign, sending]
              description: Type of workflow
            http_method:
              type: string
              enum: [GET, POST, BOTH]
              default: BOTH
            workflow_data:
              type: object
              description: Workflow configuration data
            is_public:
              type: boolean
              default: true
    responses:
      201:
        description: Workflow created successfully
        schema:
          $ref: '#/definitions/Workflow'
      400:
        description: Validation error
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from api.services.workflow_service import WorkflowService
        
        user = get_current_user()
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        if 'name' not in data:
            return jsonify({'error': 'Workflow name is required'}), 400
        if 'workflow_type' not in data:
            return jsonify({'error': 'Workflow type is required'}), 400
        
        workflow_service = WorkflowService()
        workflow = workflow_service.create_workflow(
            name=data['name'],
            workflow_type=data['workflow_type'],
            description=data.get('description', ''),
            workflow_data=data.get('workflow_data', {}),
            user=user,
            is_public=data.get('is_public', True)
        )
        
        return jsonify(workflow.to_dict()), 201
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f"Error creating workflow: {e}")
        return jsonify({
            'error': 'InternalServerError',
            'message': 'Failed to create workflow'
        }), 500

@api_bp.route('/workflows/<int:workflow_id>', methods=['GET'])

@require_auth
def get_workflow(workflow_id):
    """
    Get a single workflow by ID
    ---
    tags:
      - Workflows
    security:
      - Bearer: []
    parameters:
      - name: workflow_id
        in: path
        type: integer
        required: true
        description: Workflow ID
    responses:
      200:
        description: Workflow details
        schema:
          $ref: '#/definitions/Workflow'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Workflow not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from shared.database import WorkflowNode
        import logging
        logger = logging.getLogger(__name__)
        
        user = get_current_user()
        workflow = Workflow.query.get_or_404(workflow_id)
        
        # Check permissions
        if not workflow.is_public and workflow.created_by_id != user.id and not user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get workflow dict
        workflow_dict = workflow.to_dict(include_data=True)
        
        # Debug: Log what we have
        logger.debug(f"Workflow {workflow_id} - workflow_data nodes: {workflow_dict.get('workflow_data', {}).get('nodes', [])}")
        logger.debug(f"Workflow {workflow_id} - WorkflowNode count: {len(workflow.nodes) if workflow.nodes else 0}")
        if workflow.nodes:
            for node in workflow.nodes:
                logger.debug(f"  WorkflowNode {node.node_id}: connections = {node.connections}")
        
        # Always merge WorkflowNode records (they're the source of truth for connections)
        # The workflow_data JSON might have stale/empty connections
        if workflow.nodes:
            nodes_data = workflow_dict.get('workflow_data', {}).get('nodes', [])
            
            # Create a map of node_id -> WorkflowNode for quick lookup
            node_map = {node.node_id: node for node in workflow.nodes}
            
            # If no nodes in workflow_data, build entirely from WorkflowNode records
            if not nodes_data:
                nodes_data = []
                for node in workflow.nodes:
                    node_dict = {
                        'node_id': node.node_id,
                        'node_type': node.node_type,
                        'plugin_type': node.plugin_type,
                        'position_x': node.position_x,
                        'position_y': node.position_y,
                        'config': node.config or {},
                        'connections': node.connections or []
                    }
                    nodes_data.append(node_dict)
            else:
                # Merge connections from WorkflowNode records into existing nodes
                # WorkflowNode records are the source of truth for connections
                for node_data in nodes_data:
                    node_id = node_data.get('node_id') or node_data.get('id')
                    if node_id and node_id in node_map:
                        workflow_node = node_map[node_id]
                        # Always use connections from WorkflowNode records (they're authoritative)
                        if workflow_node.connections is not None:
                            # Ensure it's a list
                            if isinstance(workflow_node.connections, list):
                                node_data['connections'] = workflow_node.connections
                            elif isinstance(workflow_node.connections, str):
                                import json
                                try:
                                    node_data['connections'] = json.loads(workflow_node.connections)
                                except:
                                    node_data['connections'] = []
                            else:
                                node_data['connections'] = []
                        else:
                            # If WorkflowNode has no connections, use empty array
                            node_data['connections'] = []
            
            # Update workflow_data with nodes (now with correct connections)
            if 'workflow_data' not in workflow_dict:
                workflow_dict['workflow_data'] = {}
            workflow_dict['workflow_data']['nodes'] = nodes_data
        
        return jsonify(workflow_dict), 200
        
    except Exception as e:
        logger.exception(f"Error getting workflow {workflow_id}: {e}")
        return jsonify({
            'error': 'InternalServerError',
            'message': 'Failed to get workflow'
        }), 500

@api_bp.route('/workflows/<int:workflow_id>', methods=['PUT'])
@require_auth
def update_workflow(workflow_id):
    """
    Update an existing workflow
    ---
    tags:
      - Workflows
    security:
      - Bearer: []
    parameters:
      - name: workflow_id
        in: path
        type: integer
        required: true
        description: Workflow ID
      - in: body
        name: workflow
        schema:
          type: object
          properties:
            name:
              type: string
            description:
              type: string
            workflow_data:
              type: object
              description: Workflow configuration data
            is_public:
              type: boolean
            is_active:
              type: boolean
    responses:
      200:
        description: Workflow updated successfully
        schema:
          $ref: '#/definitions/Workflow'
      400:
        description: Validation error
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Workflow not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from api.services.workflow_service import WorkflowService
        
        user = get_current_user()
        workflow = Workflow.query.get_or_404(workflow_id)
        
        # Check permissions
        if not workflow.is_public and workflow.created_by_id != user.id and not user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        workflow_service = WorkflowService()
        updates = {
            'name': data.get('name'),
            'description': data.get('description'),
            'workflow_data': data.get('workflow_data'),
            'is_public': data.get('is_public'),
            'is_active': data.get('is_active')
        }
        # Remove None values
        updates = {k: v for k, v in updates.items() if v is not None}
        
        workflow = workflow_service.update_workflow(workflow, updates)
        
        return jsonify(workflow.to_dict()), 200
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f"Error updating workflow: {e}")
        return jsonify({
            'error': 'InternalServerError',
            'message': 'Failed to update workflow'
        }), 500

@api_bp.route('/workflows/<int:workflow_id>', methods=['DELETE'])
@require_auth
def delete_workflow(workflow_id):
    """
    Delete a workflow
    ---
    tags:
      - Workflows
    security:
      - Bearer: []
    parameters:
      - name: workflow_id
        in: path
        type: integer
        required: true
        description: Workflow ID
    responses:
      200:
        description: Workflow deleted successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      400:
        description: Workflow in use by campaigns
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Workflow not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from api.services.workflow_service import WorkflowService
        
        user = get_current_user()
        workflow = Workflow.query.get_or_404(workflow_id)
        
        # Check permissions - only owner or admin can delete
        if workflow.created_by_id != user.id and not user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        workflow_service = WorkflowService()
        
        # Delete workflow (this will check for dependencies)
        workflow_name = workflow.name
        workflow_service.delete_workflow(workflow)
        
        logger.info(f"Workflow '{workflow_name}' (ID: {workflow_id}) deleted by user {user.username}")
        
        return jsonify({
            'success': True,
            'message': f'Workflow "{workflow_name}" deleted successfully'
        }), 200
        
    except ValueError as e:
        # Workflow is in use by campaigns
        return jsonify({
            'error': str(e)
        }), 400
    except Exception as e:
        logger.exception(f"Error deleting workflow {workflow_id}: {e}")
        return jsonify({
            'error': 'InternalServerError',
            'message': 'Failed to delete workflow'
        }), 500

@api_bp.route('/workflows/<int:workflow_id>/export', methods=['GET'])

@require_auth
def export_workflow(workflow_id):
    """
    Export workflow configuration as ZIP archive
    ---
    tags:
      - Workflows
    security:
      - Bearer: []
    parameters:
      - name: workflow_id
        in: path
        type: integer
        required: true
        description: Workflow ID
    responses:
      200:
        description: Workflow export file (ZIP)
        schema:
          type: file
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      403:
        description: Access denied
        schema:
          $ref: '#/definitions/Error'
      404:
        description: Workflow not found
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from flask import Response
        from api.services.workflow_service import WorkflowService
        from datetime import datetime
        
        workflow = Workflow.query.get_or_404(workflow_id)
        user = get_current_user()
        
        # Check permissions
        if not workflow.is_public and workflow.created_by_id != user.id and not user.is_admin:
            return jsonify({'error': 'Access denied'}), 403
        
        workflow_service = WorkflowService()
        export_data = workflow_service.export_workflow(workflow_id)
        
        # Create ZIP archive
        zip_data = workflow_service._create_export_zip(export_data)
        
        # Generate filename
        safe_name = "".join(c for c in workflow.name if c.isalnum() or c in ('-', '_', ' ')).strip().replace(' ', '-')
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        filename = f"workflow-{safe_name}-{timestamp}.zip"
        
        return Response(
            zip_data,
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(len(zip_data))
            }
        )
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f"Error exporting workflow {workflow_id}: {e}")
        return jsonify({
            'error': 'InternalServerError',
            'message': 'Failed to export workflow'
        }), 500

@api_bp.route('/workflows/import', methods=['POST'])
@require_auth
def import_workflow():
    """
    Import workflow from ZIP archive
    ---
    tags:
      - Workflows
    security:
      - Bearer: []
    parameters:
      - name: file
        in: formData
        type: file
        required: true
        description: ZIP file containing workflow configuration
    responses:
      201:
        description: Workflow imported successfully
        schema:
          $ref: '#/definitions/Workflow'
      400:
        description: Validation error or import failed
        schema:
          $ref: '#/definitions/Error'
      401:
        description: Unauthorized
        schema:
          $ref: '#/definitions/Error'
      500:
        description: Internal server error
        schema:
          $ref: '#/definitions/Error'
    """
    try:
        from api.services.workflow_service import WorkflowService
        import zipfile
        from io import BytesIO
        
        user = get_current_user()
        
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Validate file extension
        if not file.filename.endswith('.zip'):
            return jsonify({'error': 'Only ZIP files are allowed'}), 400
        
        # Check file size (max 10MB for workflow exports)
        file_data = file.read()
        max_size = 10 * 1024 * 1024  # 10MB
        if len(file_data) > max_size:
            return jsonify({'error': 'File too large (max 10MB)'}), 400
        
        # Validate it's a valid ZIP
        try:
            zip_buffer = BytesIO(file_data)
            with zipfile.ZipFile(zip_buffer, 'r') as test_zip:
                file_list = test_zip.namelist()
                if 'workflow.json' not in file_list:
                    return jsonify({'error': 'Invalid export: workflow.json not found'}), 400
        except zipfile.BadZipFile:
            return jsonify({'error': 'Invalid ZIP file'}), 400
        
        # Get optional workflow name from form data
        workflow_name = request.form.get('name')
        
        workflow_service = WorkflowService()
        result = workflow_service.import_workflow(file_data, user, workflow_name=workflow_name)
        
        if result['success']:
            response_data = {
                'success': True,
                'workflow': result['workflow'],
                'warnings': result.get('warnings', []),
                'message': 'Workflow imported successfully'
            }
            status_code = 201
        else:
            response_data = {
                'success': False,
                'errors': result.get('errors', []),
                'warnings': result.get('warnings', []),
                'message': 'Workflow import failed'
            }
            status_code = 400
        
        return jsonify(response_data), status_code
        
    except ValueError as e:
        return jsonify({'error': str(e), 'success': False}), 400
    except Exception as e:
        logger.exception(f"Error importing workflow: {e}")
        return jsonify({
            'error': 'InternalServerError',
            'message': 'Failed to import workflow',
            'success': False
        }), 500

# Notification endpoints
@api_bp.route('/notifications', methods=['GET'])
@require_auth
def list_notifications():
    """
    List notifications for current user
    ---
    tags:
      - Notifications
    security:
      - Bearer: []
    parameters:
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number for pagination
      - name: per_page
        in: query
        type: integer
        default: 20
        maximum: 100
        description: Number of items per page
      - name: unread_only
        in: query
        type: boolean
        default: false
        description: Filter to show only unread notifications
    responses:
      200:
        description: List of notifications
        schema:
          type: object
          properties:
            notifications:
              type: array
              items:
                $ref: '#/definitions/Notification'
            pagination:
              $ref: '#/definitions/Pagination'
      401:
        description: Unauthorized
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        unread_only = request.args.get('unread_only', 'false').lower() == 'true'
        
        query = Notification.query.filter_by(user_id=current_user.id)
        
        if unread_only:
            query = query.filter_by(is_read=False)
        
        notifications = query.order_by(Notification.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'notifications': [n.to_dict() for n in notifications.items],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': notifications.total,
                'pages': notifications.pages
            }
        })
        
    except Exception as e:
        logger.exception("Error listing notifications")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/notifications', methods=['POST'])
@require_auth
def create_notification():
    """
    Create a new notification
    ---
    tags:
      - Notifications
    security:
      - Bearer: []
    parameters:
      - in: body
        name: notification
        required: true
        schema:
          type: object
          required:
            - message
            - type
          properties:
            message:
              type: string
            type:
              type: string
              enum: [success, error, warning, info]
            metadata:
              type: object
    responses:
      201:
        description: Notification created
        schema:
          $ref: '#/definitions/Notification'
      400:
        description: Validation error
      401:
        description: Unauthorized
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        message = data.get('message')
        notification_type = data.get('type', 'info')
        extra_data = data.get('metadata', {})  # Accept 'metadata' in API but store as 'extra_data'
        
        if not message:
            return jsonify({'error': 'Message is required'}), 400
        
        if notification_type not in ['success', 'error', 'warning', 'info']:
            return jsonify({'error': 'Invalid notification type'}), 400
        
        notification = Notification(
            user_id=current_user.id,
            message=message,
            type=notification_type,
            extra_data=extra_data
        )
        
        db.session.add(notification)
        db.session.commit()
        
        return jsonify(notification.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        logger.exception("Error creating notification")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/notifications/<int:notification_id>/read', methods=['PUT'])
@require_auth
def mark_notification_read(notification_id):
    """
    Mark a notification as read
    ---
    tags:
      - Notifications
    security:
      - Bearer: []
    parameters:
      - name: notification_id
        in: path
        type: integer
        required: true
    responses:
      200:
        description: Notification marked as read
        schema:
          $ref: '#/definitions/Notification'
      404:
        description: Notification not found
      401:
        description: Unauthorized
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401
        
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=current_user.id
        ).first_or_404()
        
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = datetime.utcnow()
            db.session.commit()
        
        return jsonify(notification.to_dict())
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error marking notification {notification_id} as read")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/notifications/read-all', methods=['PUT'])
@require_auth
def mark_all_notifications_read():
    """
    Mark all notifications as read for current user
    ---
    tags:
      - Notifications
    security:
      - Bearer: []
    responses:
      200:
        description: All notifications marked as read
        schema:
          type: object
          properties:
            success:
              type: boolean
            updated_count:
              type: integer
      401:
        description: Unauthorized
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401
        
        updated_count = Notification.query.filter_by(
            user_id=current_user.id,
            is_read=False
        ).update({
            'is_read': True,
            'read_at': datetime.utcnow()
        })
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'updated_count': updated_count
        })
        
    except Exception as e:
        db.session.rollback()
        logger.exception("Error marking all notifications as read")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/notifications/unread-count', methods=['GET'])
@require_auth
def get_unread_count():
    """
    Get count of unread notifications for current user
    ---
    tags:
      - Notifications
    security:
      - Bearer: []
    responses:
      200:
        description: Unread notification count
        schema:
          type: object
          properties:
            count:
              type: integer
      401:
        description: Unauthorized
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401
        
        count = Notification.query.filter_by(
            user_id=current_user.id,
            is_read=False
        ).count()
        
        return jsonify({'count': count})
        
    except Exception as e:
        logger.exception("Error getting unread count")
        return jsonify({'error': str(e)}), 500

# User password change endpoint
@api_bp.route('/users/change-password', methods=['PUT'])
@require_auth
def change_password():
    """
    Change user password with complexity validation
    ---
    tags:
      - Users
    security:
      - Bearer: []
    parameters:
      - in: body
        name: password_change
        required: true
        schema:
          type: object
          required:
            - current_password
            - new_password
            - confirm_password
          properties:
            current_password:
              type: string
            new_password:
              type: string
            confirm_password:
              type: string
    responses:
      200:
        description: Password changed successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      400:
        description: Validation error
        schema:
          type: object
          properties:
            error:
              type: string
            errors:
              type: array
              items:
                type: string
      401:
        description: Unauthorized
      403:
        description: Current password incorrect
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        confirm_password = data.get('confirm_password')
        
        if not current_password or not new_password or not confirm_password:
            return jsonify({'error': 'All password fields are required'}), 400
        
        # Verify current password
        if not current_user.check_password(current_password):
            return jsonify({'error': 'Current password is incorrect'}), 403
        
        # Check if new password matches confirmation
        if new_password != confirm_password:
            return jsonify({
                'error': 'Passwords do not match',
                'errors': ['New password and confirmation password must match']
            }), 400
        
        # Validate password complexity
        from shared.auth import validate_password_complexity
        is_valid, errors = validate_password_complexity(new_password)
        
        if not is_valid:
            return jsonify({
                'error': 'Password does not meet complexity requirements',
                'errors': errors
            }), 400
        
        # Update password
        current_user.set_password(new_password)
        current_user.password_reset_required = False  # Clear the reset requirement flag
        db.session.commit()
        
        logger.info(f"Password changed for user {current_user.username}")
        
        return jsonify({
            'success': True,
            'message': 'Password changed successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.exception("Error changing password")
        return jsonify({'error': str(e)}), 500

# ---- MMS Card Config endpoints ----

@api_bp.route('/mms-card-configs/extract-colors', methods=['POST'])
@require_auth
def mms_card_extract_colors():
    """Upload a logo and extract brand colors."""
    import re as _re
    from shared.mms_card import extract_brand_colors

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400

    # Sanitize filename
    safe_name = _re.sub(r'[^a-zA-Z0-9._-]', '_', f.filename)
    logo_dir = Path('storage/assets/mms_logos')
    logo_dir.mkdir(parents=True, exist_ok=True)
    logo_path = logo_dir / safe_name
    f.save(str(logo_path))

    colors = extract_brand_colors(str(logo_path))
    return jsonify({
        'logo_path': str(logo_path),
        'colors': colors,
    })


@api_bp.route('/mms-card-configs', methods=['POST'])
@require_auth
def mms_card_create():
    """Create a new MMS card config."""
    data = request.get_json(silent=True) or {}
    config_id = (data.get('id') or '').strip()
    if not config_id or not all(c.isalnum() or c in '-_' for c in config_id):
        return jsonify({'error': 'id is required and must be alphanumeric/hyphens/underscores'}), 400

    key = f'mms_card_{config_id}'
    # Check for duplicates
    existing = Setting.query.filter_by(key=key).first()
    if existing:
        return jsonify({'error': f'Card config "{config_id}" already exists'}), 409

    Setting.set_setting(key, data, value_type='json', category='mms_cards')
    return jsonify({'success': True, 'id': config_id}), 201


@api_bp.route('/mms-card-configs', methods=['GET'])
@require_auth
def mms_card_list():
    """List all MMS card configs."""
    settings = Setting.query.filter_by(category='mms_cards').all()
    configs = []
    for s in settings:
        val = s.get_value()
        if isinstance(val, dict):
            val['_key'] = s.key
            configs.append(val)
    return jsonify({'configs': configs})


@api_bp.route('/mms-card-configs/<config_id>', methods=['GET'])
@require_auth
def mms_card_get(config_id):
    """Get a single MMS card config."""
    val = Setting.get_setting(f'mms_card_{config_id}')
    if val is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(val)


@api_bp.route('/mms-card-configs/<config_id>', methods=['PUT'])
@require_auth
def mms_card_update(config_id):
    """Update an existing MMS card config."""
    data = request.get_json(silent=True) or {}
    key = f'mms_card_{config_id}'
    existing = Setting.query.filter_by(key=key).first()
    if not existing:
        return jsonify({'error': 'Not found'}), 404

    # Preserve the id field
    data['id'] = config_id
    Setting.set_setting(key, data, value_type='json', category='mms_cards')
    return jsonify({'success': True, 'id': config_id})


@api_bp.route('/mms-card-configs/<config_id>', methods=['DELETE'])
@require_auth
def mms_card_delete(config_id):
    """Delete an MMS card config and optionally its logo."""
    key = f'mms_card_{config_id}'
    setting = Setting.query.filter_by(key=key).first()
    if not setting:
        return jsonify({'error': 'Not found'}), 404

    # Optionally delete the logo file
    try:
        val = setting.get_value()
        if isinstance(val, dict):
            logo_path = val.get('logo_path', '')
            if logo_path and Path(logo_path).is_file():
                Path(logo_path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to delete logo for mms_card_{config_id}: {e}")

    db.session.delete(setting)
    db.session.commit()
    return jsonify({'success': True})


@api_bp.route('/mms-card-configs/<config_id>/preview', methods=['POST'])
@require_auth
def mms_card_preview(config_id):
    """Generate a preview card image for the given config with sample target data."""
    from shared.mms_card import generate_card_image

    config_data = Setting.get_setting(f'mms_card_{config_id}')
    if not config_data or not isinstance(config_data, dict):
        return jsonify({'error': 'Not found'}), 404

    body = request.get_json(silent=True) or {}
    target_data = {
        'fn': body.get('first_name', 'John'),
        'ln': body.get('last_name', 'Doe'),
        'dc': body.get('device_code', 'ABC123'),
        'url': body.get('url', 'https://example.com'),
        'em': body.get('email', 'john@example.com'),
    }

    try:
        png_bytes = generate_card_image(config_data, target_data)
    except Exception as e:
        logger.error(f"MMS card preview failed for {config_id}: {e}", exc_info=True)
        return jsonify({'error': 'Image generation failed'}), 500

    from flask import Response
    return Response(png_bytes, mimetype='image/png')


@api_bp.errorhandler(500)
def handle_server_error(e):
    """Handle 500 errors"""
    logger.exception("API server error")
    return jsonify({
        'error': 'InternalServerError',
        'message': 'An internal server error occurred'
    }), 500
