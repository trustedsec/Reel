"""
Tests for database models
"""
import pytest
import json
from datetime import datetime
from shared.database import (
    User, Campaign, Template, Event, CredentialProxyJob, db
)


def test_credential_proxy_job_model(app, db_session, sample_campaign, sample_credentials):
    """Test CredentialProxyJob model"""
    import uuid
    with app.app_context():
        template = Template(name='Test', template_html='<html></html>')
        db_session.add(template)
        db_session.commit()
        
        campaign = Campaign(
            uid=f'test-{uuid.uuid4().hex[:8]}',
            name='Test Campaign',
            campaign_type='credential_proxy',
            template_id=template.id,
            template_html='<html></html>',
            config=sample_campaign['config']
        )
        db_session.add(campaign)
        db_session.commit()
        
        job = CredentialProxyJob(
            campaign_id=campaign.id,
            credentials=sample_credentials,
            target_sites=sample_campaign['config']['target_sites'],
            status='pending',
            ai_config=sample_campaign['config']['ai_config'],
            browser_config=sample_campaign['config']['browser_config'],
            automation_timeout=60,
            max_retries=2
        )
        db_session.add(job)
        db_session.commit()
        
        assert job.id is not None
        assert job.campaign_id == campaign.id
        assert job.credentials == sample_credentials
        assert job.status == 'pending'
        assert job.retry_count == 0
        
        # Test relationship
        assert job.campaign == campaign
        
        # Test to_dict
        job_dict = job.to_dict()
        assert job_dict['id'] == job.id
        assert job_dict['campaign_id'] == campaign.id
        assert job_dict['status'] == 'pending'
        assert 'credentials' in job_dict
        assert 'target_sites' in job_dict


def test_credential_proxy_job_status_transitions(app, db_session, sample_campaign, sample_credentials):
    """Test job status transitions"""
    import uuid
    with app.app_context():
        template = Template(name='Test', template_html='<html></html>')
        db_session.add(template)
        db_session.commit()
        
        campaign = Campaign(
            uid=f'test-{uuid.uuid4().hex[:8]}',
            name='Test',
            campaign_type='credential_proxy',
            template_id=template.id,
            template_html='<html></html>',
            config=sample_campaign['config']
        )
        db_session.add(campaign)
        db_session.commit()
        
        job = CredentialProxyJob(
            campaign_id=campaign.id,
            credentials=sample_credentials,
            target_sites=[],
            status='pending'
        )
        db_session.add(job)
        db_session.commit()
        
        # Transition to processing
        job.status = 'processing'
        job.started_at = datetime.utcnow()
        db_session.commit()
        
        assert job.status == 'processing'
        assert job.started_at is not None
        
        # Transition to completed
        job.status = 'completed'
        job.completed_at = datetime.utcnow()
        job.results = {'site1': {'success': True}}
        db_session.commit()
        
        assert job.status == 'completed'
        assert job.completed_at is not None
        assert job.results is not None


def test_template_phishing_detection_fields(app, db_session):
    """Test Template phishing detection fields"""
    with app.app_context():
        template = Template(
            name='Test Template',
            template_html='<html></html>',
            phishing_is_phishing=True,
            phishing_score=0.95,
            phishing_override=False
        )
        db_session.add(template)
        db_session.commit()
        
        assert template.phishing_is_phishing is True
        assert template.phishing_score == 0.95
        assert template.phishing_override is False
        
        # Test override
        template.phishing_override = True
        db_session.commit()
        
        assert template.phishing_override is True


def test_campaign_phishing_detection_fields(app, db_session):
    """Test Campaign phishing detection fields"""
    import uuid
    with app.app_context():
        template = Template(name='Test', template_html='<html></html>')
        db_session.add(template)
        db_session.commit()
        
        campaign = Campaign(
            uid=f'test-{uuid.uuid4().hex[:8]}',
            name='Test Campaign',
            campaign_type='credential_proxy',
            template_id=template.id,
            template_html='<html></html>',
            config={},
            phishing_is_phishing=True,
            phishing_score=0.90,
            phishing_override=False
        )
        db_session.add(campaign)
        db_session.commit()
        
        assert campaign.phishing_is_phishing is True
        assert campaign.phishing_score == 0.90
        assert campaign.phishing_override is False


