"""
Integration test fixtures and helpers
"""
import pytest
from flask import Flask
from shared.database import db, Workflow, WorkflowNode, Campaign, Template, User
from tests.helpers import create_test_user, create_test_template, create_test_campaign


@pytest.fixture
def complete_campaign_setup(db_session):
    """Create a complete campaign with template and workflow"""
    # Create template
    template = create_test_template(
        db_session,
        name='Integration Test Template',
        content='<html><body><h1>Test Campaign</h1><p>Hello {{target.name}}</p></body></html>'
    )
    
    # Create workflow
    workflow = Workflow(
        name='Integration Test Workflow',
        workflow_type='campaign',
        http_method='GET',
        workflow_data={
            'nodes': [
                {'id': 'start', 'type': 'start', 'x': 100, 'y': 100},
                {'id': 'render', 'type': 'plugin', 'plugin_type': 'render_template', 'x': 200, 'y': 100}
            ],
            'connections': [
                {'from': 'start', 'to': 'render'}
            ]
        }
    )
    db_session.add(workflow)
    db_session.commit()
    
    # Create campaign
    campaign = create_test_campaign(
        db_session,
        template_id=template.id,
        uid='integration-test-123',
        name='Integration Test Campaign',
        campaign_type='inbound',
        template_html=template.template_html
    )
    
    # Link workflow to campaign
    campaign.get_workflow_id = workflow.id
    db_session.commit()
    
    return {
        'campaign': campaign,
        'template': template,
        'workflow': workflow
    }


@pytest.fixture
def workflow_with_nodes(db_session):
    """Create a workflow with nodes and connections"""
    workflow = Workflow(
        name='Test Workflow with Nodes',
        workflow_type='campaign',
        http_method='GET',
        workflow_data={}
    )
    db_session.add(workflow)
    db_session.flush()
    
    # Create nodes
    start_node = WorkflowNode(
        workflow_id=workflow.id,
        node_type='start',
        node_id='start',
        position_x=100,
        position_y=100
    )
    
    render_node = WorkflowNode(
        workflow_id=workflow.id,
        node_type='plugin',
        node_id='render',
        plugin_type='render_template',
        position_x=200,
        position_y=100,
        config={'template_source': 'campaign', 'output_variable': 'html'}
    )
    
    end_node = WorkflowNode(
        workflow_id=workflow.id,
        node_type='end',
        node_id='end',
        position_x=300,
        position_y=100
    )
    
    db_session.add_all([start_node, render_node, end_node])
    
    # Set connections
    start_node.connections = ['render']
    render_node.connections = ['end']
    
    db_session.commit()
    db_session.refresh(workflow)
    
    return workflow
