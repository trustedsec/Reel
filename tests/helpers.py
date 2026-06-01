"""
Test helper functions
"""
import json
from typing import Dict, Any
from shared.database import Campaign, Template, User, CredentialProxyJob, db
from werkzeug.security import generate_password_hash


def create_test_user(db_session, username='testuser', email='test@example.com', is_admin=True):
    """Create a test user"""
    user = User(
        username=username,
        email=email,
        password_hash=generate_password_hash('testpass'),
        is_active=True,
        is_admin=is_admin
    )
    db_session.add(user)
    db_session.commit()
    return user


def create_test_template(db_session, name='Test Template', content='<html><body>Test</body></html>'):
    """Create a test template"""
    template = Template(
        name=name,
        template_html=content
    )
    db_session.add(template)
    db_session.commit()
    return template


def create_test_campaign(db_session, template_id=None, uid='test-123', name='Test Campaign', 
                         campaign_type='inbound', config=None, template_html='<html><body>Test</body></html>'):
    """Create a test campaign"""
    if config is None:
        config = {}
    
    campaign = Campaign(
        uid=uid if campaign_type == 'inbound' else None,
        name=name,
        campaign_type=campaign_type,
        template_id=template_id,
        template_html=template_html,
        config=config,
        status='active' if campaign_type == 'inbound' else 'draft'
    )
    db_session.add(campaign)
    db_session.commit()
    return campaign


def create_test_credential_proxy_job(db_session, campaign_id, credentials=None, 
                                     target_sites=None, status='pending'):
    """Create a test credential proxy job"""
    if credentials is None:
        credentials = {'email': 'test@example.com', 'password': 'testpass'}
    
    if target_sites is None:
        target_sites = [{'url': 'https://example.com/login', 'timeout': 30}]
    
    job = CredentialProxyJob(
        campaign_id=campaign_id,
        credentials=credentials,
        target_sites=target_sites,
        status=status
    )
    db_session.add(job)
    db_session.commit()
    return job


def assert_dict_contains(actual: Dict[str, Any], expected: Dict[str, Any]):
    """Assert that actual dict contains all keys and values from expected dict"""
    for key, value in expected.items():
        assert key in actual, f"Key '{key}' not found in actual dict"
        assert actual[key] == value, f"Value for '{key}' mismatch: expected {value}, got {actual[key]}"



def get_csrf_token(client):
    """Extract CSRF token from login page or meta tag"""
    import re
    # Try to get from login page first
    response = client.get('/login')
    if response.status_code == 200:
        # Try to extract from meta tag
        match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.data.decode('utf-8'))
        if match:
            return match.group(1)
        # Try to extract from form
        match = re.search(r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"', response.data.decode('utf-8'))
        if match:
            return match.group(1)
    
    # Fallback: try to get from any page with meta tag
    response = client.get('/')
    if response.status_code == 200:
        match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.data.decode('utf-8'))
        if match:
            return match.group(1)
    
    return None


def create_complete_campaign(db_session, template_id=None, workflow_id=None, uid=None):
    """Create campaign with all required components"""
    from shared.database import Workflow
    import uuid
    
    # Create template if not provided
    if template_id is None:
        template = create_test_template(
            db_session,
            name=f'Template {uuid.uuid4().hex[:8]}',
            content='<html><body>Test Template</body></html>'
        )
        template_id = template.id
    
    # Create workflow if not provided
    if workflow_id is None:
        workflow = Workflow(
            name=f'Workflow {uuid.uuid4().hex[:8]}',
            workflow_type='campaign',
            http_method='GET',
            workflow_data={},
            is_active=True  # Workflows must be active to execute
        )
        db_session.add(workflow)
        db_session.commit()
        workflow_id = workflow.id
    
    # Create campaign
    if uid is None:
        uid = f'test-{uuid.uuid4().hex[:8]}'
    
    campaign = create_test_campaign(
        db_session,
        template_id=template_id,
        uid=uid,
        name=f'Campaign {uuid.uuid4().hex[:8]}',
        campaign_type='inbound'
    )
    
    # Link workflow
    campaign.get_workflow_id = workflow_id
    db_session.commit()
    
    return campaign


def create_workflow_with_nodes(db_session, nodes_data, connections_data=None):
    """Create workflow with nodes and connections"""
    from shared.database import Workflow, WorkflowNode
    import uuid
    
    workflow = Workflow(
        name=f'Workflow {uuid.uuid4().hex[:8]}',
        workflow_type='campaign',
        http_method='GET',
        workflow_data={},
        is_active=True  # Workflows must be active to execute
    )
    db_session.add(workflow)
    db_session.flush()
    
    # Create nodes
    nodes = {}
    for node_data in nodes_data:
        node = WorkflowNode(
            workflow_id=workflow.id,
            node_type=node_data.get('type', 'plugin'),
            node_id=node_data['id'],
            plugin_type=node_data.get('plugin_type'),
            position_x=node_data.get('x', 100),
            position_y=node_data.get('y', 100),
            config=node_data.get('config', {})
        )
        db_session.add(node)
        nodes[node_data['id']] = node
    
    db_session.flush()
    
    # Set connections - ensure they're properly saved to database
    # JSON columns need explicit flag_modified for SQLAlchemy to detect changes
    if connections_data:
        from sqlalchemy.orm.attributes import flag_modified
        for conn in connections_data:
            from_node = nodes.get(conn['from'])
            if from_node:
                # Initialize connections as list if None
                if from_node.connections is None:
                    from_node.connections = []
                # Ensure it's a list
                if not isinstance(from_node.connections, list):
                    from_node.connections = [from_node.connections] if from_node.connections else []
                # Add connection if not already present
                if conn['to'] not in from_node.connections:
                    from_node.connections.append(conn['to'])
                # Mark as modified so SQLAlchemy saves it (required for JSON columns)
                flag_modified(from_node, 'connections')
    
    db_session.flush()  # Flush to ensure connections are saved
    db_session.commit()
    db_session.refresh(workflow)
    
    # Refresh nodes to ensure connections are loaded from DB
    for node in nodes.values():
        db_session.refresh(node)
    
    return workflow


def execute_workflow_integration(workflow, context, db_session):
    """Execute workflow with full integration setup"""
    from workflows.engine import WorkflowEngine
    
    # Setup campaign in context if needed
    if 'campaign' not in context:
        campaign = create_test_campaign(db_session)
        context['campaign'] = campaign.to_dict(include_template=True)
    
    engine = WorkflowEngine()
    return engine.execute_workflow(workflow.id, context)
