"""
Target selector plugin - selects email and phone targets from various sources
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
import logging
import csv
from io import StringIO
from pathlib import Path

logger = logging.getLogger(__name__)

class TargetSelectorPlugin(BasePlugin):
    """
    Plugin for selecting email and phone targets from various sources.

    Loads target email addresses and/or phone numbers with associated data from
    CSV files, database queries, manual entry, or tracked users from campaigns.
    Supports filtering and stores targets in context for use by sending workflows.

    Use cases:
    - Load targets from CSV file for email or SMS campaigns
    - Select tracked users from a campaign
    - Manually specify target list with email and/or phone
    - Filter targets by domain, phone prefix, or other criteria
    - Prepare target lists for outbound workflows (email, SMS, calls)

    Example: Load targets from CSV with email, phone, first_name columns,
    filter to only @company.com domain, then use in SMTP or SMS sender workflow.
    """
    
    @property
    def plugin_type(self) -> str:
        return "target_selector"
    
    @property
    def display_name(self) -> str:
        return "Target Selector"
    
    @property
    def description(self) -> str:
        return "Select email targets from CSV files, database queries, manual entry, or tracked users. Supports filtering by domain and count limits. Stores targets in context for use by sending workflows (email, phone calls, etc.)."
    
    @property
    def plugin_category(self) -> str:
        return "target_selection"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_type": {
                    "type": "string",
                    "title": "Target Source Type",
                    "enum": [
                        {
                            "value": "csv",
                            "label": "CSV Data",
                            "description": "Load targets from CSV data string or file path. Best for importing target lists from spreadsheets or external sources."
                        },
                        {
                            "value": "database",
                            "label": "Database Query",
                            "description": "Load targets from database query (not yet implemented). Future feature for querying internal database."
                        },
                        {
                            "value": "manual",
                            "label": "Manual Entry",
                            "description": "Manually specify target list in configuration. Best for small, fixed target lists or testing."
                        },
                        {
                            "value": "tracked_users",
                            "label": "Tracked Users",
                            "description": "Load targets from tracked users of a specific campaign. Use to re-engage users who visited a campaign."
                        }
                    ],
                    "description": "Source of target email addresses",
                    "help": "Choose where to load targets from: CSV for file imports, manual for small lists, tracked_users for campaign visitors. Database option is not yet implemented.",
                },
                "csv_data": {
                    "type": "string",
                    "title": "CSV Data",
                    "description": "CSV data (paste multi-line). First row = headers.",
                    "help": "Paste CSV with headers (email, first_name, last_name, etc.). Use this or select a file below.",
                    "placeholder": "email,first_name,last_name\nuser@example.com,John,Doe",
                    "format": "textarea"
                },
                "csv_file_path": {
                    "type": "string",
                    "title": "CSV File",
                    "description": "Select a CSV file from the server or enter path",
                    "help": "Choose a file from the list (files in storage/uploads/targets) or enter a custom path. Use this or paste CSV above.",
                    "placeholder": "storage/uploads/targets/targets.csv",
                    "format": "file_select"
                },
                "email_column": {
                    "type": "string",
                    "title": "Email Column Name",
                    "description": "CSV column name containing email addresses",
                    "help": "Name of the CSV column that contains email addresses. Default: 'email'. Other common names: 'Email', 'email_address', 'e-mail'. The column must exist in your CSV.",
                    "placeholder": "email, Email, email_address",
                    "default": "email"
                },
                "phone_column": {
                    "type": "string",
                    "title": "Phone Column Name",
                    "description": "CSV column name containing phone numbers",
                    "help": "Name of the CSV column that contains phone numbers. Default: 'phone'. Other common names: 'Phone', 'phone_number', 'mobile'. Leave default if your CSV has no phone column.",
                    "placeholder": "phone, Phone, phone_number",
                    "default": "phone"
                },
                "campaign_id": {
                    "type": "integer",
                    "title": "Campaign ID",
                    "description": "Campaign ID to load tracked users from",
                    "help": "Campaign ID to load tracked users from. Only used when source_type is 'tracked_users'. Loads all users who have visited/interacted with this campaign. Can use context variable: {{campaign.id}}",
                    "placeholder": "1, 5, {{campaign.id}}"
                },
                "manual_targets": {
                    "type": "array",
                    "title": "Manual Target List",
                    "items": {
                        "type": "object",
                        "properties": {
                            "email": {
                                "type": "string",
                                "format": "email",
                                "title": "Email Address",
                                "description": "Target email address (required unless phone is provided)"
                            },
                            "phone": {
                                "type": "string",
                                "title": "Phone Number",
                                "description": "Target phone number (required unless email is provided)"
                            },
                            "first_name": {
                                "type": "string",
                                "title": "First Name",
                                "description": "Target first name (optional)"
                            },
                            "last_name": {
                                "type": "string",
                                "title": "Last Name",
                                "description": "Target last name (optional)"
                            },
                            "custom_data": {
                                "type": "object",
                                "title": "Custom Data",
                                "description": "Additional custom data fields (optional)"
                            }
                        },
                        "required": []
                    },
                    "description": "Manually specified list of targets",
                    "help": "Array of target objects with email (required), first_name, last_name, and custom_data (optional). Use for small, fixed target lists. Example: [{'email': 'user@example.com', 'first_name': 'John', 'last_name': 'Doe'}]"
                },
                "filter": {
                    "type": "object",
                    "title": "Filter Criteria",
                    "description": "Filter criteria to apply to targets",
                    "help": "Optional filter to apply to loaded targets. Supports 'email_domain' (filter by email domain like '@company.com') and 'max_count' (limit number of targets). Example: {'email_domain': '@company.com', 'max_count': 100}",
                    "properties": {
                        "email_domain": {
                            "type": "string",
                            "description": "Filter by email domain (e.g., '@company.com')"
                        },
                        "phone_prefix": {
                            "type": "string",
                            "description": "Filter by phone number prefix (e.g., '+1', '+44')"
                        },
                        "max_count": {
                            "type": "integer",
                            "description": "Maximum number of targets to return"
                        }
                    }
                }
            },
            "required": ["source_type"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute target selection"""
        try:
            source_type = config['source_type']
            targets = []
            
            if source_type == 'csv':
                targets = self._load_from_csv(config, context)
            
            elif source_type == 'database':
                targets = self._load_from_database(config, context)
            
            elif source_type == 'manual':
                targets = config.get('manual_targets', [])
            
            elif source_type == 'tracked_users':
                targets = self._load_tracked_users(config, context)
            
            # Apply filters if specified
            if config.get('filter'):
                targets = self._apply_filter(targets, config['filter'])
            
            # Store targets in context
            context['targets'] = targets
            context['target_count'] = len(targets)
            
            logger.info(f"Selected {len(targets)} targets from {source_type}")
            
        except Exception as e:
            logger.error(f"Target selection failed: {e}")
            return self.on_error(e, context, config)
        
        return context
    
    def _load_from_csv(self, config: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Load targets from CSV (inline csv_data or file at csv_file_path)."""
        targets = []
        email_column = config.get('email_column', 'email')
        phone_column = config.get('phone_column', 'phone')

        csv_data = config.get('csv_data')
        csv_file_path = (config.get('csv_file_path') or '').strip()
        if csv_file_path:
            csv_data = self._read_csv_file(csv_file_path)
        if not csv_data:
            csv_data = config.get('csv_data')

        if csv_data:
            reader = csv.DictReader(StringIO(csv_data))
            for row in reader:
                email = (row.get(email_column) or '').strip()
                phone = (row.get(phone_column) or '').strip()
                # Include row if it has email OR phone
                if not email and not phone:
                    continue
                exclude_cols = {email_column, phone_column}
                target = {
                    'email': email,
                    'phone': phone,
                    'first_name': row.get('first_name', ''),
                    'last_name': row.get('last_name', ''),
                    'custom_data': {k: v for k, v in row.items() if k not in exclude_cols}
                }
                targets.append(target)

        return targets

    def _read_csv_file(self, csv_file_path: str) -> Optional[str]:
        """Read CSV content from a file path. Path is resolved relative to cwd; must be under cwd or storage."""
        try:
            p = Path(csv_file_path)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            else:
                p = p.resolve()
            base = Path.cwd().resolve()
            storage = (Path.cwd() / "storage").resolve()
            if base not in p.parents and storage not in p.parents and p != base and p != storage:
                logger.warning(f"CSV path not under cwd or storage: {csv_file_path}")
                return None
            if not p.exists() or not p.is_file():
                logger.warning(f"CSV file not found: {p}")
                return None
            return p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to read CSV file {csv_file_path}: {e}")
            return None
    
    def _load_from_database(self, config: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Load targets from database query"""
        # This would query the database based on config
        # For now, return empty list
        logger.warning("Database target selection not yet implemented")
        return []
    
    def _load_tracked_users(self, config: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Load targets from tracked users"""
        from shared.database import TrackedUser
        
        campaign_id = config.get('campaign_id') or context.get('campaign_id')
        if not campaign_id:
            logger.error("campaign_id required for tracked_users source")
            return []
        
        tracked_users = TrackedUser.query.filter_by(campaign_id=campaign_id).all()
        targets = []
        
        for user in tracked_users:
            targets.append({
                'email': user.email or '',
                'phone': getattr(user, 'phone', '') or '',
                'first_name': user.first_name or '',
                'last_name': user.last_name or '',
                'tracking_id': user.tracking_id,
                'custom_data': {
                    'department': user.department
                }
            })
        
        return targets
    
    def _apply_filter(self, targets: List[Dict[str, Any]], filter_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply filter to targets"""
        filtered = targets

        if 'email_domain' in filter_config:
            domain = filter_config['email_domain']
            filtered = [t for t in filtered if t.get('email', '').endswith(f'@{domain}')]

        if 'phone_prefix' in filter_config:
            prefix = filter_config['phone_prefix']
            filtered = [t for t in filtered if t.get('phone', '').startswith(prefix)]

        if 'max_count' in filter_config:
            max_count = filter_config['max_count']
            filtered = filtered[:max_count]

        return filtered
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate target selector configuration"""
        errors = []
        
        source_type = config.get('source_type')
        if not source_type:
            errors.append("source_type is required")
        elif source_type not in ['csv', 'database', 'manual', 'tracked_users']:
            errors.append(f"Invalid source_type: {source_type}")
        
        if source_type == 'csv':
            if not config.get('csv_data') and not config.get('csv_file_path'):
                errors.append("csv_data or csv_file_path required for CSV source")
        
        if source_type == 'tracked_users':
            if not config.get('campaign_id'):
                errors.append("campaign_id required for tracked_users source")
        
        if source_type == 'manual':
            if not config.get('manual_targets'):
                errors.append("manual_targets required for manual source")
            else:
                for i, target in enumerate(config['manual_targets']):
                    if not target.get('email') and not target.get('phone'):
                        errors.append(f"manual_targets[{i}] must have at least email or phone")

        return errors if errors else None

