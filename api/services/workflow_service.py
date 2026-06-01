"""
Workflow service for managing workflows
"""
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from shared.database import db, Workflow, WorkflowNode, User, Campaign
from workflows.engine import execute_workflow
import logging
import uuid

logger = logging.getLogger(__name__)

class WorkflowService:
    """Service for workflow operations"""
    
    def create_workflow(
        self,
        name: str,
        workflow_type: str,
        description: str,
        workflow_data: Dict[str, Any],
        user: User,
        is_public: bool = True
    ) -> Workflow:
        """Create a new workflow"""
        try:
            if workflow_type not in ['campaign', 'sending']:
                raise ValueError(f"Invalid workflow_type: {workflow_type}")
            
            workflow = Workflow(
                name=name,
                description=description,
                workflow_type=workflow_type,
                workflow_data=workflow_data,
                is_public=is_public,
                is_active=True,
                created_by_id=user.id
            )
            
            db.session.add(workflow)
            db.session.flush()  # Get workflow ID
            
            # Create nodes from workflow_data
            if 'nodes' in workflow_data:
                self._create_nodes_from_data(workflow.id, workflow_data['nodes'])
            
            db.session.commit()
            
            logger.info(f"Workflow created: {name} (ID: {workflow.id}) by user {user.username}")
            return workflow
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to create workflow: {e}")
            raise
    
    def update_workflow(
        self,
        workflow: Workflow,
        updates: Dict[str, Any]
    ) -> Workflow:
        """Update a workflow"""
        try:
            if 'name' in updates:
                workflow.name = updates['name']
            if 'description' in updates:
                workflow.description = updates['description']
            if 'workflow_data' in updates:
                workflow.workflow_data = updates['workflow_data']
                
                # Update nodes
                if 'nodes' in updates['workflow_data']:
                    # Delete existing nodes properly (transactional)
                    existing_nodes = WorkflowNode.query.filter_by(workflow_id=workflow.id).all()
                    for node in existing_nodes:
                        db.session.delete(node)
                    db.session.flush()  # Ensure deletions are processed before creating new nodes
                    # Create new nodes
                    self._create_nodes_from_data(workflow.id, updates['workflow_data']['nodes'])
            
            if 'is_public' in updates:
                workflow.is_public = updates['is_public']
            if 'is_active' in updates:
                workflow.is_active = updates['is_active']
            
            workflow.updated_at = datetime.utcnow()
            db.session.commit()
            
            logger.info(f"Workflow updated: {workflow.name} (ID: {workflow.id})")
            return workflow
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to update workflow: {e}")
            raise
    
    def delete_workflow(self, workflow: Workflow):
        """Delete a workflow"""
        try:
            # Check if workflow is in use by campaigns (check both get_workflow_id and post_workflow_id)
            campaigns_using_get = Campaign.query.filter_by(get_workflow_id=workflow.id).all()
            campaigns_using_post = Campaign.query.filter_by(post_workflow_id=workflow.id).all()
            
            # Combine and deduplicate campaigns
            all_campaigns = list(set(campaigns_using_get + campaigns_using_post))
            
            if all_campaigns:
                campaign_names = [c.name for c in all_campaigns]
                raise ValueError(
                    f"Cannot delete workflow: {len(all_campaigns)} campaign(s) are using it: {', '.join(campaign_names)}"
                )
            
            db.session.delete(workflow)
            db.session.commit()
            
            logger.info(f"Workflow deleted: {workflow.name} (ID: {workflow.id})")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to delete workflow: {e}")
            raise
    
    def assign_workflow_to_campaign(self, campaign: Campaign, workflow_id: Optional[int]):
        """Assign a workflow to a campaign"""
        try:
            if workflow_id:
                workflow = Workflow.query.get(workflow_id)
                if not workflow:
                    raise ValueError(f"Workflow {workflow_id} not found")
                if workflow.workflow_type != 'campaign':
                    raise ValueError(f"Workflow {workflow_id} is not a campaign workflow")
                campaign.workflow_id = workflow_id
            else:
                campaign.workflow_id = None
            
            campaign.updated_at = datetime.utcnow()
            db.session.commit()
            
            logger.info(f"Workflow {workflow_id} assigned to campaign {campaign.name}")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to assign workflow: {e}")
            raise
    
    def _validate_connections(self, nodes: List[Dict[str, Any]], node_ids: set) -> List[str]:
        """Validate all connection formats (simple strings and path-based dicts)"""
        errors = []
        for node in nodes:
            node_id = node.get('node_id')
            connections = node.get('connections', [])
            
            if not isinstance(connections, list):
                errors.append(f"Node {node_id}: connections must be a list")
                continue
            
            for i, conn in enumerate(connections):
                if isinstance(conn, str):
                    # Simple format: ['node1', 'node2']
                    if conn not in node_ids:
                        errors.append(f"Node {node_id}: connection[{i}] references non-existent node '{conn}'")
                elif isinstance(conn, dict):
                    # Path-based format: [{'target': 'node1', 'path': 'success'}]
                    if 'target' not in conn:
                        errors.append(f"Node {node_id}: connection[{i}] missing 'target' field")
                    elif conn['target'] not in node_ids:
                        errors.append(f"Node {node_id}: connection[{i}] references non-existent node '{conn['target']}'")
                    if 'path' not in conn:
                        errors.append(f"Node {node_id}: connection[{i}] missing 'path' field")
                else:
                    errors.append(f"Node {node_id}: connection[{i}] has invalid format (must be string or dict)")
        
        return errors
    
    def _validate_workflow_structure(self, workflow_data: Dict[str, Any]) -> List[str]:
        """
        Validate workflow structure before processing (fail fast)
        
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        if 'nodes' not in workflow_data:
            errors.append("Workflow must have nodes")
            return errors
        
        nodes = workflow_data.get('nodes', [])
        if not isinstance(nodes, list):
            errors.append("Nodes must be a list")
            return errors
        
        if len(nodes) == 0:
            errors.append("Workflow must have at least one node")
            return errors
        
        # Check for start node
        start_nodes = [n for n in nodes if n.get('node_type') == 'start']
        if len(start_nodes) == 0:
            errors.append("Workflow must have a start node")
        elif len(start_nodes) > 1:
            errors.append("Workflow can only have one start node")
        
        # Validate node IDs
        node_ids = set()
        for i, node in enumerate(nodes):
            node_id = node.get('node_id')
            if not node_id:
                errors.append(f"Node {i+1}: node_id is required")
            elif not isinstance(node_id, str) or not node_id.strip():
                errors.append(f"Node {i+1}: node_id must be a non-empty string")
            elif node_id in node_ids:
                errors.append(f"Node {i+1}: duplicate node_id '{node_id}'")
            else:
                node_ids.add(node_id)
        
        # Validate connections
        connection_errors = self._validate_connections(nodes, node_ids)
        errors.extend(connection_errors)
        
        return errors
    
    def _remap_node_ids(self, workflow_data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """
        Remap node IDs to ensure uniqueness
        
        Fixes:
        - Missing/null node IDs
        - Duplicate node IDs
        
        Returns:
            (remapped_workflow_data, id_mapping) where id_mapping is {old_id: new_id}
        """
        nodes = workflow_data.get('nodes', [])
        id_mapping = {}
        seen_ids = set()
        
        # First pass: identify and fix duplicates and missing IDs
        for node in nodes:
            old_id = node.get('node_id')
            
            # Handle missing or invalid node IDs
            if not old_id or not isinstance(old_id, str) or not old_id.strip():
                # Missing or invalid ID - generate new one
                new_id = f"node_{uuid.uuid4().hex[:8]}"
                if old_id:  # If old_id exists but is invalid, track the remapping
                    id_mapping[old_id] = new_id
                node['node_id'] = new_id
                logger.warning(f"Generated new node_id '{new_id}' for node with missing/invalid ID (was: {old_id})")
            elif old_id in seen_ids:
                # Duplicate ID - generate new one
                new_id = f"node_{uuid.uuid4().hex[:8]}"
                id_mapping[old_id] = new_id
                node['node_id'] = new_id
                logger.warning(f"Remapped duplicate node_id '{old_id}' to '{new_id}'")
            else:
                # Valid unique ID
                new_id = old_id
            
            seen_ids.add(new_id)
        
        # Second pass: update connections using the mapping
        for node in nodes:
            connections = node.get('connections', [])
            if not isinstance(connections, list):
                continue
            
            updated_connections = []
            for conn in connections:
                if isinstance(conn, str):
                    # Simple format: remap if needed
                    updated_connections.append(id_mapping.get(conn, conn))
                elif isinstance(conn, dict) and 'target' in conn:
                    # Path-based format: remap target if needed
                    new_conn = conn.copy()
                    new_conn['target'] = id_mapping.get(conn['target'], conn['target'])
                    updated_connections.append(new_conn)
                else:
                    # Keep invalid formats as-is (will be caught by validation)
                    updated_connections.append(conn)
            
            node['connections'] = updated_connections
        
        return workflow_data, id_mapping
    
    def validate_workflow(self, workflow_data: Dict[str, Any]) -> List[str]:
        """Validate workflow structure and plugin configurations"""
        from api.services.plugin_service import PluginService
        
        errors = []
        plugin_service = PluginService()
        
        # First validate structure
        structure_errors = self._validate_workflow_structure(workflow_data)
        if structure_errors:
            errors.extend(structure_errors)
            # Don't continue with plugin validation if structure is invalid
            return errors
        
        nodes = workflow_data['nodes']
        node_ids = {node.get('node_id') for node in nodes if node.get('node_id')}
        
        # Validate plugin configurations
        for node in nodes:
            node_id = node.get('node_id')
            node_type = node.get('node_type')
            
            if node_type == 'plugin':
                plugin_type = node.get('plugin_type')
                if not plugin_type:
                    errors.append(f"Node {node_id}: plugin_type is required for plugin nodes")
                else:
                    # Validate plugin config against schema
                    plugin = plugin_service.get_plugin(plugin_type)
                    if plugin:
                        config = node.get('config', {})
                        config_errors = plugin_service.validate_plugin_config(plugin_type, config)
                        if config_errors:
                            errors.extend([f"Node {node_id}: {e}" for e in config_errors])
        
        return errors
    
    def _create_nodes_from_data(self, workflow_id: int, nodes_data: List[Dict[str, Any]]):
        """Create workflow nodes from data"""
        from shared.database import Plugin
        
        for node_data in nodes_data:
            # Deduplicate connections before saving
            connections = node_data.get('connections', [])
            if isinstance(connections, list) and len(connections) > 0:
                # Check if connections are path-based (objects with 'path' and 'target')
                if isinstance(connections[0], dict) and 'path' in connections[0]:
                    # Path-based format: each target can only appear on ONE path (no duplicates)
                    # Keep first occurrence per target - later duplicates are bogus (e.g. from sync/merge bugs)
                    seen_targets: set = set()
                    unique_connections = []
                    for conn in connections:
                        if isinstance(conn, dict) and 'target' in conn and 'path' in conn:
                            target = conn.get('target')
                            if target not in seen_targets:
                                seen_targets.add(target)
                                unique_connections.append(conn)
                            else:
                                logger.warning(
                                    f"Duplicate target {target} with path {conn.get('path')} removed for node "
                                    f"{node_data.get('node_id')} (each target can only be on one path)"
                                )
                    connections = unique_connections
                else:
                    # Simple format: deduplicate by target
                    connections = list(dict.fromkeys(connections))  # Preserves order while removing duplicates
            
            node = WorkflowNode(
                workflow_id=workflow_id,
                node_id=node_data.get('node_id', str(uuid.uuid4())),
                node_type=node_data.get('node_type', 'plugin'),
                position_x=node_data.get('position_x', 0),
                position_y=node_data.get('position_y', 0),
                config=node_data.get('config', {}),
                connections=connections
            )
            
            # Set plugin_id if plugin_type is specified
            plugin_type = node_data.get('plugin_type')
            if plugin_type:
                plugin = Plugin.query.filter_by(plugin_type=plugin_type).first()
                if plugin:
                    node.plugin_id = plugin.id
                node.plugin_type = plugin_type
            
            db.session.add(node)
    
    def clone_workflow(self, workflow: Workflow, user: User) -> Workflow:
        """Clone a workflow"""
        try:
            cloned_workflow = Workflow(
                name=f"{workflow.name} (Copy)",
                description=workflow.description,
                workflow_type=workflow.workflow_type,
                workflow_data=workflow.workflow_data.copy() if workflow.workflow_data else {},
                is_public=False,  # Cloned workflows start as private
                is_active=True,
                created_by_id=user.id
            )
            
            db.session.add(cloned_workflow)
            db.session.flush()
            
            # Clone nodes
            for node in workflow.nodes:
                cloned_node = WorkflowNode(
                    workflow_id=cloned_workflow.id,
                    node_id=node.node_id,
                    node_type=node.node_type,
                    plugin_id=node.plugin_id,
                    plugin_type=node.plugin_type,
                    position_x=node.position_x,
                    position_y=node.position_y,
                    config=node.config.copy() if node.config else {},
                    connections=node.connections.copy() if node.connections else []
                )
                db.session.add(cloned_node)
            
            db.session.commit()
            
            logger.info(f"Workflow cloned: {workflow.name} -> {cloned_workflow.name}")
            return cloned_workflow
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to clone workflow: {e}")
            raise
    
    def test_workflow(self, workflow_id: int, test_context: Dict[str, Any]) -> Dict[str, Any]:
        """Test execute a workflow with test context"""
        try:
            result_context = execute_workflow(
                workflow_id,
                test_context,
                hook_name='test'
            )
            return result_context
        except Exception as e:
            logger.error(f"Workflow test failed: {e}")
            raise
    
    def export_workflow(self, workflow_id: int) -> Dict[str, Any]:
        """
        Export workflow configuration with plugin information
        
        Returns:
            Dict containing workflow data, plugin information, and metadata
        """
        try:
            from shared.database import Plugin
            from pathlib import Path
            
            workflow = Workflow.query.get(workflow_id)
            if not workflow:
                raise ValueError(f"Workflow {workflow_id} not found")
            
            # Get all nodes
            nodes = workflow.nodes or []
            
            # Identify plugins used in workflow
            plugin_types = set()
            custom_plugins = []
            builtin_plugins = []
            
            for node in nodes:
                if node.plugin_type:
                    plugin_types.add(node.plugin_type)
            
            # Categorize plugins
            for plugin_type in plugin_types:
                plugin = Plugin.query.filter_by(plugin_type=plugin_type).first()
                if plugin:
                    if plugin.is_builtin:
                        builtin_plugins.append(plugin_type)
                    else:
                        # Read plugin source code
                        plugin_data = {
                            'plugin_type': plugin.plugin_type,
                            'name': plugin.name,
                            'description': plugin.description,
                            'plugin_category': plugin.plugin_category,
                            'config_schema': plugin.config_schema or {},
                            'code': None
                        }
                        
                        # Read plugin code if code_path exists
                        if plugin.code_path:
                            try:
                                code_path = Path(plugin.code_path)
                                if code_path.exists():
                                    plugin_data['code'] = code_path.read_text(encoding='utf-8')
                                else:
                                    logger.warning(f"Plugin code file not found: {plugin.code_path}")
                            except Exception as e:
                                logger.warning(f"Failed to read plugin code from {plugin.code_path}: {e}")
                        
                        custom_plugins.append(plugin_data)
            
            # Build export structure
            export_data = {
                'version': '1.0',
                'exported_at': datetime.utcnow().isoformat(),
                'workflow': {
                    'name': workflow.name,
                    'description': workflow.description,
                    'workflow_type': workflow.workflow_type,
                    'http_method': workflow.http_method,
                    'workflow_data': workflow.workflow_data or {},
                    'nodes': [node.to_dict() for node in nodes]
                },
                'plugins': {
                    'builtin': builtin_plugins,
                    'custom': custom_plugins
                }
            }
            
            return export_data
            
        except Exception as e:
            logger.error(f"Failed to export workflow {workflow_id}: {e}")
            raise
    
    def _create_export_zip(self, export_data: Dict[str, Any]) -> bytes:
        """
        Create ZIP archive from export data
        
        Args:
            export_data: Export data dictionary
            
        Returns:
            ZIP file as bytes
        """
        import zipfile
        import json
        from io import BytesIO
        
        zip_buffer = BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add workflow.json
            workflow_json = json.dumps(export_data['workflow'], indent=2)
            zip_file.writestr('workflow.json', workflow_json)
            
            # Add manifest.json
            manifest = {
                'version': export_data.get('version', '1.0'),
                'exported_at': export_data.get('exported_at'),
                'workflow_name': export_data['workflow']['name'],
                'workflow_type': export_data['workflow']['workflow_type'],
                'plugins': {
                    'builtin': export_data['plugins']['builtin'],
                    'custom': [p['plugin_type'] for p in export_data['plugins']['custom']]
                },
                'dependencies': {
                    p['plugin_type']: {
                        'required': True,
                        'code_included': p.get('code') is not None
                    }
                    for p in export_data['plugins']['custom']
                }
            }
            manifest_json = json.dumps(manifest, indent=2)
            zip_file.writestr('manifest.json', manifest_json)
            
            # Add plugins.json with metadata
            plugins_metadata = {
                'custom': [
                    {
                        'plugin_type': p['plugin_type'],
                        'name': p['name'],
                        'description': p['description'],
                        'plugin_category': p['plugin_category'],
                        'config_schema': p['config_schema']
                    }
                    for p in export_data['plugins']['custom']
                ]
            }
            plugins_metadata_json = json.dumps(plugins_metadata, indent=2)
            zip_file.writestr('plugins.json', plugins_metadata_json)
            
            # Add custom plugin files
            for plugin in export_data['plugins']['custom']:
                if plugin.get('code'):
                    plugin_filename = f"plugins/{plugin['plugin_type']}.py"
                    zip_file.writestr(plugin_filename, plugin['code'])
        
        zip_buffer.seek(0)
        return zip_buffer.read()
    
    def import_workflow(self, zip_data: bytes, user: User, workflow_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Import workflow from ZIP archive
        
        Args:
            zip_data: ZIP file bytes
            user: User importing the workflow
            
        Returns:
            Dict with import result (workflow, warnings, errors)
        """
        import zipfile
        import json
        from io import BytesIO
        from shared.database import Plugin
        from api.services.plugin_service import PluginService
        from pathlib import Path
        from shared.config import Config
        
        errors = []
        warnings = []
        
        try:
            # Extract ZIP archive
            zip_buffer = BytesIO(zip_data)
            
            with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
                # Validate structure
                file_list = zip_file.namelist()
                if 'workflow.json' not in file_list:
                    raise ValueError("Invalid export: workflow.json not found")
                if 'manifest.json' not in file_list:
                    raise ValueError("Invalid export: manifest.json not found")
                
                # Parse manifest
                manifest_data = json.loads(zip_file.read('manifest.json').decode('utf-8'))
                
                # Parse workflow
                workflow_data = json.loads(zip_file.read('workflow.json').decode('utf-8'))
                
                # Validate workflow structure FIRST (before plugin installation - fail fast)
                structure_errors = self._validate_workflow_structure(workflow_data)
                if structure_errors:
                    raise ValueError("Workflow structure validation failed:\n" + "\n".join(f"- {e}" for e in structure_errors))
                
                # Remap node IDs if needed (auto-fix duplicates and missing IDs)
                remapped_workflow_data, id_mapping = self._remap_node_ids(workflow_data)
                if id_mapping:
                    # Log remapping for user feedback
                    remap_messages = [f"Node ID '{old_id}' remapped to '{new_id}'" for old_id, new_id in id_mapping.items()]
                    warnings.extend([f"Node ID remapping: {msg}" for msg in remap_messages])
                    logger.info(f"Remapped {len(id_mapping)} node IDs during import")
                # Always use remapped data (even if no remapping occurred, it's the same object)
                workflow_data = remapped_workflow_data
                
                # Check custom plugins
                plugin_service = PluginService()
                custom_plugins = manifest_data.get('plugins', {}).get('custom', [])
                
                for plugin_type in custom_plugins:
                    # Check if plugin exists
                    existing_plugin = Plugin.query.filter_by(plugin_type=plugin_type).first()
                    
                    if existing_plugin:
                        # Plugin exists - validate compatibility
                        plugin_file = f"plugins/{plugin_type}.py"
                        if plugin_file in file_list:
                            exported_code = zip_file.read(plugin_file).decode('utf-8')
                            
                            # Read existing plugin code
                            if existing_plugin.code_path:
                                existing_code_path = Path(existing_plugin.code_path)
                                if existing_code_path.exists():
                                    existing_code = existing_code_path.read_text(encoding='utf-8')
                                    
                                    # Compare code
                                    if existing_code != exported_code:
                                        errors.append(
                                            f"Plugin '{plugin_type}' exists but code differs. "
                                            f"Import blocked to prevent conflicts."
                                        )
                                    else:
                                        warnings.append(
                                            f"Plugin '{plugin_type}' already exists with matching code. Using existing plugin."
                                        )
                                else:
                                    warnings.append(
                                        f"Plugin '{plugin_type}' exists but code file not found. Using existing plugin record."
                                    )
                            else:
                                warnings.append(
                                    f"Plugin '{plugin_type}' exists but has no code path. Using existing plugin record."
                                )
                        else:
                            warnings.append(
                                f"Plugin '{plugin_type}' exists but no code in export. Using existing plugin."
                            )
                    else:
                        # Plugin doesn't exist - need to install it
                        plugin_file = f"plugins/{plugin_type}.py"
                        if plugin_file not in file_list:
                            errors.append(
                                f"Required custom plugin '{plugin_type}' not found in export and not installed."
                            )
                        else:
                            # Extract and install plugin
                            try:
                                plugin_code = zip_file.read(plugin_file).decode('utf-8')
                                
                                # Get plugin metadata from plugins.json
                                plugin_metadata = None
                                if 'plugins.json' in file_list:
                                    try:
                                        plugins_data = json.loads(zip_file.read('plugins.json').decode('utf-8'))
                                        for p in plugins_data.get('custom', []):
                                            if p.get('plugin_type') == plugin_type:
                                                plugin_metadata = p
                                                break
                                    except Exception as e:
                                        logger.warning(f"Failed to parse plugins.json: {e}")
                                
                                # Fallback to defaults if metadata not found (will use loaded plugin properties)
                                if not plugin_metadata:
                                    plugin_metadata = {}
                                
                                # Save plugin code
                                plugins_dir = Path('storage/plugins')
                                plugins_dir.mkdir(parents=True, exist_ok=True)
                                
                                plugin_filename = f"{plugin_type}.py"
                                plugin_path = plugins_dir / plugin_filename
                                plugin_path.write_text(plugin_code, encoding='utf-8')
                                
                                # Load and validate plugin
                                registry = plugin_service.registry
                                loaded_plugin = registry.load_plugin_from_path(str(plugin_path))
                                
                                if not loaded_plugin:
                                    errors.append(f"Failed to load plugin '{plugin_type}' from export")
                                    plugin_path.unlink()  # Clean up
                                else:
                                    # Validate plugin
                                    validation_errors = registry.validate_plugin(loaded_plugin)
                                    if validation_errors:
                                        errors.append(
                                            f"Plugin '{plugin_type}' validation failed: {', '.join(validation_errors)}"
                                        )
                                        plugin_path.unlink()  # Clean up
                                    else:
                                        # Use metadata from plugins.json if available, otherwise use loaded plugin properties
                                        plugin_name = plugin_metadata.get('name')
                                        if not plugin_name:
                                            plugin_name = loaded_plugin.display_name
                                        
                                        plugin_category = plugin_metadata.get('plugin_category')
                                        if not plugin_category:
                                            plugin_category = loaded_plugin.plugin_category
                                        
                                        plugin_description = plugin_metadata.get('description')
                                        if not plugin_description:
                                            plugin_description = loaded_plugin.description
                                        
                                        plugin_config_schema = plugin_metadata.get('config_schema')
                                        if not plugin_config_schema:
                                            plugin_config_schema = loaded_plugin.config_schema
                                        
                                        # Create plugin record
                                        db_plugin = plugin_service.create_custom_plugin(
                                            name=plugin_name,
                                            plugin_type=plugin_type,
                                            plugin_category=plugin_category,
                                            description=plugin_description,
                                            code_path=str(plugin_path),
                                            config_schema=plugin_config_schema,
                                            user=user
                                        )
                                        logger.info(f"Installed custom plugin '{plugin_type}' during workflow import")
                            
                            except Exception as e:
                                errors.append(f"Failed to install plugin '{plugin_type}': {str(e)}")
                                logger.exception(f"Error installing plugin {plugin_type}")
                
                # If there are errors, don't proceed with import
                if errors:
                    raise ValueError("Import failed due to plugin issues:\n" + "\n".join(f"- {e}" for e in errors))
                
                # Validate all referenced plugins exist
                all_plugin_types = set()
                for node in workflow_data.get('nodes', []):
                    if node.get('plugin_type'):
                        all_plugin_types.add(node['plugin_type'])
                
                for plugin_type in all_plugin_types:
                    plugin = Plugin.query.filter_by(plugin_type=plugin_type).first()
                    if not plugin:
                        errors.append(f"Plugin '{plugin_type}' referenced in workflow but not found")
                
                if errors:
                    raise ValueError("Import failed: missing plugins:\n" + "\n".join(f"- {e}" for e in errors))
                
                # Use provided name if given, otherwise use name from export
                if workflow_name:
                    workflow_data['name'] = workflow_name
                
                # Create workflow (using remapped data if remapping occurred)
                workflow = Workflow.import_from_data(workflow_data, user.id)
                
                # Final validation (includes plugin config validation)
                validation_errors = self.validate_workflow(workflow.workflow_data)
                if validation_errors:
                    raise ValueError(f"Workflow validation failed:\n" + "\n".join(f"- {e}" for e in validation_errors))
                
                # Save to database
                db.session.add(workflow)
                db.session.commit()
                
                logger.info(f"Workflow imported: {workflow.name} (ID: {workflow.id}) by user {user.username}")
                
                return {
                    'success': True,
                    'workflow': workflow.to_dict(),
                    'warnings': warnings,
                    'errors': []
                }
                
        except ValueError as e:
            db.session.rollback()
            return {
                'success': False,
                'workflow': None,
                'warnings': warnings,
                'errors': [str(e)]
            }
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Failed to import workflow: {e}")
            return {
                'success': False,
                'workflow': None,
                'warnings': warnings,
                'errors': [f"Import failed: {str(e)}"]
            }

