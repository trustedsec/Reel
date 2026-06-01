"""
Plugin service for managing plugins
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from shared.database import db, Plugin, User
from plugins import get_registry, get_plugin
import logging

logger = logging.getLogger(__name__)

class PluginService:
    """Service for plugin operations"""
    
    def __init__(self):
        self.registry = get_registry()
    
    def list_plugins(self, category: str = None, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all available plugins"""
        try:
            # Get plugins from registry
            registry_plugins = self.registry.list_plugins(category)
            
            # Get plugins from database
            query = Plugin.query
            if active_only:
                query = query.filter(Plugin.is_active == True)
            if category:
                query = query.filter(Plugin.plugin_category == category)
            
            db_plugins = query.all()
            
            # Merge registry and database plugins
            plugin_map = {p['plugin_type']: p for p in registry_plugins}
            
            for db_plugin in db_plugins:
                if db_plugin.plugin_type in plugin_map:
                    # Update with database info
                    plugin_map[db_plugin.plugin_type].update(db_plugin.to_dict())
                else:
                    # Add database-only plugin
                    plugin_map[db_plugin.plugin_type] = db_plugin.to_dict()
            
            return list(plugin_map.values())
            
        except Exception as e:
            logger.error(f"Failed to list plugins: {e}")
            raise
    
    def get_plugin(self, plugin_type: str) -> Optional[Dict[str, Any]]:
        """Get plugin by type"""
        try:
            # Try registry first
            plugin = self.registry.get_plugin(plugin_type)
            if plugin:
                return plugin.to_dict()
            
            # Try database
            db_plugin = Plugin.query.filter_by(plugin_type=plugin_type).first()
            if db_plugin:
                return db_plugin.to_dict()
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get plugin {plugin_type}: {e}")
            raise
    
    def sync_builtin_plugins(self):
        """Sync builtin plugins to database"""
        try:
            registry_plugins = self.registry.list_plugins()
            
            for plugin_data in registry_plugins:
                plugin_type = plugin_data['plugin_type']
                
                # Check if plugin exists in database
                db_plugin = Plugin.query.filter_by(plugin_type=plugin_type).first()
                
                if not db_plugin:
                    # Create new plugin record
                    db_plugin = Plugin(
                        name=plugin_data['display_name'],
                        description=plugin_data['description'],
                        plugin_type=plugin_type,
                        plugin_category=plugin_data['plugin_category'],
                        config_schema=plugin_data['config_schema'],
                        is_builtin=True,
                        is_active=True
                    )
                    db.session.add(db_plugin)
                else:
                    # Update existing plugin
                    db_plugin.name = plugin_data['display_name']
                    db_plugin.description = plugin_data['description']
                    db_plugin.plugin_category = plugin_data['plugin_category']
                    db_plugin.config_schema = plugin_data['config_schema']
                    db_plugin.updated_at = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"Synced {len(registry_plugins)} builtin plugins to database")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to sync builtin plugins: {e}")
            raise
    
    def create_custom_plugin(
        self,
        name: str,
        plugin_type: str,
        plugin_category: str,
        description: str,
        code_path: str,
        config_schema: Dict[str, Any],
        user: User
    ) -> Plugin:
        """Create a custom plugin"""
        try:
            # Check if plugin type already exists
            existing = Plugin.query.filter_by(plugin_type=plugin_type).first()
            if existing:
                raise ValueError(f"Plugin type {plugin_type} already exists")
            
            plugin = Plugin(
                name=name,
                description=description,
                plugin_type=plugin_type,
                plugin_category=plugin_category,
                code_path=code_path,
                config_schema=config_schema,
                is_builtin=False,
                is_active=True,
                created_by_id=user.id
            )
            
            db.session.add(plugin)
            db.session.commit()
            
            logger.info(f"Custom plugin created: {plugin_type} by user {user.username}")
            return plugin
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to create custom plugin: {e}")
            raise
    
    def update_plugin(self, plugin: Plugin, updates: Dict[str, Any]) -> Plugin:
        """Update a plugin"""
        try:
            if 'name' in updates:
                plugin.name = updates['name']
            if 'description' in updates:
                plugin.description = updates['description']
            if 'config_schema' in updates:
                plugin.config_schema = updates['config_schema']
            if 'is_active' in updates:
                plugin.is_active = updates['is_active']
            
            plugin.updated_at = datetime.utcnow()
            db.session.commit()
            
            logger.info(f"Plugin updated: {plugin.plugin_type}")
            return plugin
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to update plugin: {e}")
            raise
    
    def delete_plugin(self, plugin: Plugin):
        """Delete a plugin (only custom plugins can be deleted)"""
        try:
            if plugin.is_builtin:
                raise ValueError("Cannot delete builtin plugins")
            
            db.session.delete(plugin)
            db.session.commit()
            
            logger.info(f"Plugin deleted: {plugin.plugin_type}")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to delete plugin: {e}")
            raise
    
    def validate_plugin_config(self, plugin_type: str, config: Dict[str, Any]) -> List[str]:
        """Validate plugin configuration"""
        try:
            plugin = get_plugin(plugin_type)
            if not plugin:
                return [f"Plugin {plugin_type} not found"]
            
            errors = plugin.validate_config(config)
            return errors or []
            
        except Exception as e:
            logger.error(f"Failed to validate plugin config: {e}")
            return [str(e)]

