"""
Workflow execution engine
"""
from typing import Dict, Any, Optional, List
from shared.database import db, Workflow, WorkflowNode, WorkflowExecution, Campaign
from plugins import get_plugin
from datetime import datetime
import logging
import json
import copy

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """Engine for executing workflows"""
    
    def __init__(self):
        self.execution_cache = {}  # Cache for active executions
    
    def execute_workflow(
        self,
        workflow_id: int,
        context: Dict[str, Any],
        campaign_id: Optional[int] = None,
        hook_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute a workflow with given context
        
        Args:
            workflow_id: ID of workflow to execute
            context: Execution context
            campaign_id: Optional campaign ID
            hook_name: Optional hook name for tracking
            
        Returns:
            Modified context after execution
        """
        try:
            workflow = Workflow.query.get(workflow_id)
            if not workflow:
                raise ValueError(f"Workflow {workflow_id} not found")
            
            if not workflow.is_active:
                logger.warning(f"Workflow {workflow_id} is not active, skipping execution")
                return context
            
            # Create execution record
            execution = WorkflowExecution(
                workflow_id=workflow_id,
                campaign_id=campaign_id,
                execution_context=context.copy(),
                status='running',
                started_at=datetime.utcnow()
            )
            db.session.add(execution)
            db.session.commit()
            
            # Add execution metadata to context
            context['_workflow_execution_id'] = execution.id
            context['_workflow_id'] = workflow_id
            context['_hook_name'] = hook_name
            context['_timestamp'] = datetime.utcnow().isoformat()
            
            # Execute workflow
            try:
                result_context = self._execute_workflow_internal(workflow, context)
                
                execution.status = 'completed'
                execution.completed_at = datetime.utcnow()
                db.session.commit()
                
                logger.info(f"Workflow {workflow_id} executed successfully")
                return result_context
                
            except Exception as e:
                execution.status = 'failed'
                execution.error_message = str(e)
                execution.completed_at = datetime.utcnow()
                db.session.commit()
                
                logger.error(f"Workflow {workflow_id} execution failed: {e}", exc_info=True)
                raise
        
        except Exception as e:
            logger.error(f"Workflow execution error: {e}", exc_info=True)
            db.session.rollback()
            return context
    
    def _execute_workflow_internal(self, workflow: Workflow, context: Dict[str, Any]) -> Dict[str, Any]:
        """Internal workflow execution logic"""
        # Get workflow nodes
        nodes = WorkflowNode.query.filter_by(workflow_id=workflow.id).order_by(WorkflowNode.id).all()
        
        if not nodes:
            logger.warning(f"Workflow {workflow.id} has no nodes")
            return context
        
        # Build node graph
        node_map = {node.node_id: node for node in nodes}
        
        # Find start node
        start_nodes = [n for n in nodes if n.node_type == 'start']
        if not start_nodes:
            # If no start node, use first node
            start_node = nodes[0]
        else:
            start_node = start_nodes[0]
        
        # Execute workflow starting from start node
        executed_nodes = set()
        return self._execute_node(start_node, node_map, context, executed_nodes)
    
    def _execute_node(
        self,
        node: WorkflowNode,
        node_map: Dict[str, WorkflowNode],
        context: Dict[str, Any],
        executed_nodes: set
    ) -> Dict[str, Any]:
        """Execute a single node and follow connections"""
        # Prevent infinite loops (but allow END nodes to be reached multiple times)
        if node.node_id in executed_nodes:
            # END nodes can be reached from multiple paths - that's fine, just return
            if node.node_type == 'end':
                return context
            logger.warning(f"Node {node.node_id} already executed, skipping")
            return context
        
        executed_nodes.add(node.node_id)
        
        try:
            # Execute node based on type
            if node.node_type == 'start':
                # Start node - just pass through
                pass
            
            elif node.node_type == 'end':
                # End node - stop execution
                return context
            
            elif node.node_type == 'plugin':
                # Execute plugin
                context = self._execute_plugin_node(node, context)
            
            elif node.node_type == 'condition':
                # Handle conditional branching
                context = self._execute_condition_node(node, context)
            
            elif node.node_type == 'loop':
                # Handle loop node - iterate over array
                context = self._execute_loop_node(node, node_map, context, executed_nodes)
                # Loop node handles its own connections, so return early
                return context
            
            # If a plugin set _stop_workflow, halt execution (e.g. sync proxy
            # rendered its own MFA/error page and must NOT be overridden)
            if context.get('_stop_workflow'):
                logger.info(f"Node {node.node_id} set _stop_workflow, halting downstream execution")
                return context

            # Follow connections
            connections = node.connections or []
            if connections:
                # Check if connections use boolean path format (new format)
                is_boolean_path_format = (
                    isinstance(connections, list) and 
                    len(connections) > 0 and 
                    isinstance(connections[0], dict) and 
                    'path' in connections[0] and 
                    'target' in connections[0]
                )
                
                if is_boolean_path_format:
                    # Boolean path format: [{"path": "true", "target": "node-id"}, ...]
                    # Deduplicate: each target can only appear once (first occurrence wins)
                    seen_targets = set()
                    deduped = []
                    for conn in connections:
                        if isinstance(conn, dict):
                            t = conn.get('target')
                            if t and t not in seen_targets:
                                seen_targets.add(t)
                                deduped.append(conn)
                    connections = deduped
                    
                    resolved_path = self._resolve_boolean_path(node, context)
                    
                    if resolved_path:
                        # Follow only connections matching the resolved path
                        for conn in connections:
                            if isinstance(conn, dict):
                                conn_path = conn.get('path')
                                # Normalize path values (handle "success" as "true", "fail" as "false")
                                if conn_path == 'success':
                                    conn_path = 'true'
                                elif conn_path == 'fail':
                                    conn_path = 'false'
                                
                                if conn_path == resolved_path:
                                    target_id = conn.get('target')
                                    if target_id and target_id in node_map:
                                        next_node = node_map[target_id]
                                        context = self._execute_node(next_node, node_map, context, executed_nodes)
                    else:
                        # No path resolved, skip connections (or could follow default if exists)
                        logger.debug(f"Node {node.node_id} has boolean paths but no path was resolved. Context keys: {list(context.keys())}")
                elif node.node_type == 'condition' and '_condition_branch' in context:
                    # Legacy conditional node handling (for backward compatibility)
                    next_node_id = context.get('_condition_branch')
                    if next_node_id and next_node_id in node_map:
                        next_node = node_map[next_node_id]
                        context = self._execute_node(next_node, node_map, context, executed_nodes)
                else:
                    # Simple string array format (backward compatibility)
                    # Execute all connected nodes (parallel execution)
                    for next_node_id in connections:
                        if isinstance(next_node_id, str) and next_node_id in node_map:
                            next_node = node_map[next_node_id]
                            context = self._execute_node(next_node, node_map, copy.deepcopy(context), executed_nodes)
            
            return context
            
        except Exception as e:
            logger.error(f"Error executing node {node.node_id}: {e}", exc_info=True)
            context['_error'] = {
                'node_id': node.node_id,
                'error': str(e)
            }
            return context
    
    def _execute_plugin_node(self, node: WorkflowNode, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a plugin node"""
        plugin_type = node.plugin_type
        
        if not plugin_type:
            logger.error(f"Node {node.node_id} has no plugin_type")
            return context
        
        # Get plugin
        plugin = get_plugin(plugin_type)
        if not plugin:
            logger.error(f"Plugin {plugin_type} not found for node {node.node_id}")
            return context
        
        # Get node configuration
        config = node.config or {}
        
        # Validate configuration
        validation_errors = plugin.validate_config(config)
        if validation_errors:
            logger.error(f"Plugin {plugin_type} config validation failed: {validation_errors}")
            context['_validation_errors'] = validation_errors
            return context
        
        # Execute plugin
        try:
            result_context = plugin.execute(context.copy(), config)
            
            # Validate plugin return value
            if result_context is None:
                logger.warning(f"Plugin {plugin_type} returned None, using original context")
                return context
            
            if not isinstance(result_context, dict):
                logger.error(f"Plugin {plugin_type} returned invalid type {type(result_context)}, using original context")
                return context
            
            logger.debug(f"Plugin {plugin_type} executed successfully")
            return result_context
        except Exception as e:
            logger.error(f"Plugin {plugin_type} execution failed: {e}", exc_info=True)
            try:
                error_result = plugin.on_error(e, context, config)
                if error_result is None or not isinstance(error_result, dict):
                    logger.warning(f"Plugin {plugin_type} on_error returned invalid value, using original context")
                    return context
                return error_result
            except Exception as error_handler_error:
                logger.error(f"Plugin {plugin_type} on_error handler failed: {error_handler_error}", exc_info=True)
                return context
    
    def _execute_condition_node(self, node: WorkflowNode, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a condition node"""
        # Condition nodes use the conditional plugin
        plugin = get_plugin('conditional')
        if not plugin:
            logger.error("Conditional plugin not found")
            return context
        
        config = node.config or {}
        config['condition'] = config.get('condition', {})
        
        # Execute conditional plugin
        return plugin.execute(context, config)
    
    def _execute_loop_node(
        self,
        node: WorkflowNode,
        node_map: Dict[str, WorkflowNode],
        context: Dict[str, Any],
        executed_nodes: set
    ) -> Dict[str, Any]:
        """
        Execute a loop node - iterate over an array and execute child nodes for each item
        
        Args:
            node: Loop node to execute
            node_map: Map of all nodes in workflow
            context: Execution context
            executed_nodes: Set of already executed nodes (to prevent infinite loops)
            
        Returns:
            Modified context
        """
        config = node.config or {}
        array_source = config.get('array_source', 'targets')
        item_key = config.get('item_key', 'target')
        index_key = config.get('index_key', '_loop_index')
        delay = config.get('delay', 0)  # Delay in seconds between iterations
        
        # Get array from context
        array = context.get(array_source)
        if not isinstance(array, list):
            logger.warning(f"Loop node {node.node_id}: '{array_source}' is not a list or not found in context")
            return context
        
        if not array:
            logger.info(f"Loop node {node.node_id}: Array '{array_source}' is empty, skipping loop")
            return context
        
        # Initialize loop results
        loop_results = []
        
        # Get child nodes (nodes connected from this loop node)
        connections = node.connections or []
        child_node_ids = []
        
        # Extract child node IDs from connections
        for conn in connections:
            if isinstance(conn, dict):
                child_node_ids.append(conn.get('target'))
            elif isinstance(conn, str):
                child_node_ids.append(conn)
        
        if not child_node_ids:
            logger.warning(f"Loop node {node.node_id} has no child nodes to execute")
            return context
        
        # Iterate over array
        for index, item in enumerate(array):
            try:
                # Create iteration context (copy of original)
                iteration_context = copy.deepcopy(context)
                
                # Set current item in context
                iteration_context[item_key] = item
                iteration_context[index_key] = index
                
                # Execute child nodes for this iteration
                for child_node_id in child_node_ids:
                    if child_node_id and child_node_id in node_map:
                        child_node = node_map[child_node_id]
                        # Use a fresh executed_nodes set for each iteration to allow re-execution
                        iteration_executed = set()
                        iteration_context = self._execute_node(
                            child_node,
                            node_map,
                            iteration_context,
                            iteration_executed
                        )
                
                # Store iteration result: success if any sending channel succeeded
                iteration_result = {
                    'index': index,
                    'item': item,
                    'success': bool(
                        iteration_context.get('_email_sent')
                        or (iteration_context.get('_sms_result') or {}).get('success')
                        or (iteration_context.get('_call_result') or {}).get('success')
                    ),
                    'context': iteration_context
                }
                loop_results.append(iteration_result)
                
                # Update main context with iteration results (merge, don't replace)
                # This allows plugins to add data that persists across iterations
                for key, value in iteration_context.items():
                    # Don't overwrite the original array or loop metadata
                    if key not in [array_source, item_key, index_key, '_loop_results']:
                        context[key] = value
                
                # Apply delay between iterations (except last one)
                if delay > 0 and index < len(array) - 1:
                    import time
                    time.sleep(delay)
                    
            except Exception as e:
                logger.error(f"Error in loop iteration {index}: {e}", exc_info=True)
                iteration_result = {
                    'index': index,
                    'item': item,
                    'success': False,
                    'error': str(e)
                }
                loop_results.append(iteration_result)
                # Continue to next iteration
        
        # Store loop results in context
        context['_loop_results'] = loop_results
        context['_loop_total'] = len(array)
        context['_loop_successful'] = sum(1 for r in loop_results if r.get('success', False))
        context['_loop_failed'] = len(loop_results) - context['_loop_successful']
        
        logger.info(f"Loop node {node.node_id} completed: {context['_loop_successful']}/{context['_loop_total']} iterations successful")
        
        return context
    
    def _resolve_boolean_path(self, node: WorkflowNode, context: Dict[str, Any]) -> Optional[str]:
        """
        Resolve the boolean path for a node based on its plugin's branch context key.
        
        Args:
            node: The workflow node to check
            context: Execution context
            
        Returns:
            "true" or "false" if branching is supported, None otherwise
        """
        # Plugin nodes and condition nodes can have branching
        # Condition nodes use the conditional plugin
        plugin_type = node.plugin_type
        if node.node_type == 'condition':
            plugin_type = 'conditional'
        
        if not plugin_type:
            return None
        
        # Get plugin
        plugin = get_plugin(plugin_type)
        if not plugin:
            return None
        
        # Check if plugin supports branching
        branch_key = plugin.get_branch_context_key()
        if not branch_key:
            return None
        
        # Resolve nested keys (e.g., "phishing_detection.is_phishing")
        value = context
        for key_part in branch_key.split('.'):
            if isinstance(value, dict):
                value = value.get(key_part)
            else:
                return None
        
        # Convert to boolean and return path
        if isinstance(value, bool):
            return "true" if value else "false"
        
        # Handle truthy/falsy values
        return "true" if value else "false"

# Global engine instance
_engine = None

def get_engine() -> WorkflowEngine:
    """Get global workflow engine instance"""
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine

def execute_workflow(
    workflow_id: int,
    context: Dict[str, Any],
    campaign_id: Optional[int] = None,
    hook_name: Optional[str] = None
) -> Dict[str, Any]:
    """Execute a workflow"""
    return get_engine().execute_workflow(workflow_id, context, campaign_id, hook_name)

