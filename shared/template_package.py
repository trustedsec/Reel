"""
Template Package Handler - Import/Export templates as zip packages
"""
import zipfile
import json
import hashlib
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class TemplatePackageHandler:
    """Handle template zip package import/export"""
    
    def __init__(self, storage_base: Path = None):
        self.storage_base = storage_base or Path("storage")
        self.temp_dir = self.storage_base / "temp"
        self.assets_dir = self.storage_base / "assets"
        
        # Ensure directories exist
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
    
    def _detect_package_type(self, file_list: List[str]) -> str:
        """
        Detect if this is a single template or multi-template package
        
        Returns:
            'single' - Single template (template.json at root)
            'multi' - Multiple templates (template folders)
        """
        # Check for single template format (files at root)
        if 'template.json' in file_list and 'template.html' in file_list:
            return 'single'
        
        # Check for multi-template format (template directories)
        template_dirs = []
        for file_path in file_list:
            parts = file_path.split('/')
            if len(parts) >= 2 and parts[1] in ['template.json', 'template.html']:
                template_dirs.append(parts[0])
        
        if len(set(template_dirs)) > 0:
            return 'multi'
        
        return 'unknown'

    def validate_package(self, zip_path: Path) -> Tuple[bool, List[str], Optional[Dict]]:
        """
        Validate a template package (single or multi-template)
        
        Returns:
            (is_valid, errors, metadata)
        """
        errors = []
        metadata = None
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Check if it's a valid zip
                if zf.testzip() is not None:
                    errors.append("Invalid or corrupted zip file")
                    return False, errors, None
                
                file_list = zf.namelist()
                package_type = self._detect_package_type(file_list)
                
                if package_type == 'single':
                    return self._validate_single_template(zf, file_list)
                elif package_type == 'multi':
                    return self._validate_multi_template(zf, file_list)
                else:
                    errors.append("Invalid package format. Expected single template (template.json + template.html) or multi-template (template1/, template2/, etc.)")
                    return False, errors, None
        
        except zipfile.BadZipFile:
            errors.append("File is not a valid zip archive")
        except Exception as e:
            errors.append(f"Unexpected error during validation: {str(e)}")
        
        return False, errors, None

    def _validate_single_template(self, zf: zipfile.ZipFile, file_list: List[str]) -> Tuple[bool, List[str], Optional[Dict]]:
        """Validate single template package format"""
        errors = []
        metadata = None
        
        # Check required files
        if 'template.json' not in file_list:
            errors.append("Missing required file: template.json")
        
        if 'template.html' not in file_list:
            errors.append("Missing required file: template.html")
        
        # If we have required files, validate metadata
        if 'template.json' in file_list:
            try:
                metadata_content = zf.read('template.json')
                metadata = json.loads(metadata_content.decode('utf-8'))
                metadata['package_type'] = 'single'
                        
                # Validate metadata structure
                required_fields = ['name', 'description', 'category', 'template_type']
                for field in required_fields:
                    if field not in metadata:
                        errors.append(f"Missing required metadata field: {field}")
                
                # Validate template_type (match Template model: main, captcha, error + legacy)
                valid_types = ['phishing', 'landing', 'captcha', 'main', 'error']
                if metadata.get('template_type') not in valid_types:
                    errors.append(f"Invalid template_type. Must be one of: {valid_types}")
                
                # Validate variables structure if present (can be list or dict for legacy)
                if 'variables' in metadata:
                    if not isinstance(metadata['variables'], (dict, list)):
                        errors.append("Variables must be a list or dictionary")
                    elif isinstance(metadata['variables'], dict):
                        for var_name, var_config in metadata['variables'].items():
                            if not isinstance(var_config, dict):
                                errors.append(f"Variable '{var_name}' must be a dictionary")
                            elif 'type' not in var_config:
                                errors.append(f"Variable '{var_name}' missing 'type' field")
                        
            except json.JSONDecodeError as e:
                errors.append(f"Invalid JSON in template.json: {e}")
            except UnicodeDecodeError:
                errors.append("template.json contains invalid UTF-8 encoding")
        
        # Check HTML template
        if 'template.html' in file_list:
            try:
                html_content = zf.read('template.html').decode('utf-8')
                if len(html_content.strip()) == 0:
                    errors.append("template.html is empty")
            except UnicodeDecodeError:
                errors.append("template.html contains invalid UTF-8 encoding")
        
        # Check for potentially dangerous files
        dangerous_extensions = ['.exe', '.bat', '.sh', '.py', '.php', '.jsp', '.asp']
        for file_path in file_list:
            file_ext = Path(file_path).suffix.lower()
            if file_ext in dangerous_extensions:
                errors.append(f"Potentially dangerous file detected: {file_path}")
        
        # Check total size
        total_size = sum(zf.getinfo(f).file_size for f in file_list)
        max_size = 50 * 1024 * 1024  # 50MB
        if total_size > max_size:
            errors.append(f"Package too large: {total_size / 1024 / 1024:.1f}MB (max: {max_size / 1024 / 1024}MB)")
        
        is_valid = len(errors) == 0
        return is_valid, errors, metadata

    def _validate_multi_template(self, zf: zipfile.ZipFile, file_list: List[str]) -> Tuple[bool, List[str], Optional[Dict]]:
        """Validate multi-template package format"""
        errors = []
        templates = {}
        
        # Group files by template directory
        template_dirs = {}
        for file_path in file_list:
            if '/' in file_path and not file_path.endswith('/'):
                dir_name = file_path.split('/')[0]
                if dir_name not in template_dirs:
                    template_dirs[dir_name] = []
                template_dirs[dir_name].append(file_path)
        
        # Validate each template directory
        valid_template_count = 0
        for template_dir, files in template_dirs.items():
            template_json_path = f"{template_dir}/template.json"
            template_html_path = f"{template_dir}/template.html"
            
            # Check required files for this template
            if template_json_path not in file_list:
                errors.append(f"Template '{template_dir}': Missing template.json")
                continue
                
            if template_html_path not in file_list:
                errors.append(f"Template '{template_dir}': Missing template.html")
                continue
            
            # Validate metadata for this template
            try:
                metadata_content = zf.read(template_json_path)
                template_metadata = json.loads(metadata_content.decode('utf-8'))
                
                # Validate required fields
                required_fields = ['name', 'description', 'category', 'template_type']
                for field in required_fields:
                    if field not in template_metadata:
                        errors.append(f"Template '{template_dir}': Missing required field '{field}'")
                
                # Validate template_type
                valid_types = ['phishing', 'landing', 'captcha']
                if template_metadata.get('template_type') not in valid_types:
                    errors.append(f"Template '{template_dir}': Invalid template_type. Must be one of: {valid_types}")
                
                # Check HTML content
                html_content = zf.read(template_html_path).decode('utf-8')
                if len(html_content.strip()) == 0:
                    errors.append(f"Template '{template_dir}': template.html is empty")
                
                templates[template_dir] = template_metadata
                valid_template_count += 1
                
            except json.JSONDecodeError as e:
                errors.append(f"Template '{template_dir}': Invalid JSON in template.json: {e}")
            except UnicodeDecodeError:
                errors.append(f"Template '{template_dir}': Invalid UTF-8 encoding")
            except Exception as e:
                errors.append(f"Template '{template_dir}': Error validating: {e}")
        
        # Check for potentially dangerous files
        dangerous_extensions = ['.exe', '.bat', '.sh', '.py', '.php', '.jsp', '.asp']
        for file_path in file_list:
            file_ext = Path(file_path).suffix.lower()
            if file_ext in dangerous_extensions:
                errors.append(f"Potentially dangerous file detected: {file_path}")
        
        # Check total size
        total_size = sum(zf.getinfo(f).file_size for f in file_list)
        max_size = 50 * 1024 * 1024  # 50MB
        if total_size > max_size:
            errors.append(f"Package too large: {total_size / 1024 / 1024:.1f}MB (max: {max_size / 1024 / 1024}MB)")
        
        if valid_template_count == 0:
            errors.append("No valid templates found in package")
        
        # Create metadata for multi-template package
        metadata = {
            'package_type': 'multi',
            'template_count': valid_template_count,
            'templates': templates
        }
        
        is_valid = len(errors) == 0 and valid_template_count > 0
        return is_valid, errors, metadata
    
    def import_package(self, zip_path: Path, user_id: int = None) -> Tuple[bool, List[str], Optional[List[int]]]:
        """
        Import a template package (single or multi-template)
        
        Returns:
            (success, messages, template_ids) - template_ids is list for multi, single int for single
        """
        from shared.database import db, Template, Asset
        
        messages = []
        template_ids = []
        
        try:
            # Validate package first
            is_valid, errors, metadata = self.validate_package(zip_path)
            if not is_valid:
                return False, errors, None
            
            # Route to appropriate import method based on package type
            if metadata['package_type'] == 'single':
                return self._import_single_template(zip_path, user_id)
            elif metadata['package_type'] == 'multi':
                return self._import_multi_template(zip_path, user_id)
            else:
                return False, ["Unknown package type"], None
        
        except Exception as e:
            logger.exception(f"Failed to import template package: {e}")
            return False, [f"Import failed: {str(e)}"], None

    def _import_single_template(self, zip_path: Path, user_id: int = None) -> Tuple[bool, List[str], Optional[int]]:
        """Import a single template package"""
        from shared.database import db, Template

        messages = []
        template_id = None

        try:
            # Validate package first
            is_valid, errors, metadata = self.validate_package(zip_path)
            if not is_valid:
                return False, errors, None

            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Read metadata and HTML
                metadata_content = zf.read('template.json')
                metadata = json.loads(metadata_content.decode('utf-8'))
                html_content = zf.read('template.html').decode('utf-8')

                variables = metadata.get('variables')
                if isinstance(variables, dict):
                    variables = []
                elif not isinstance(variables, list):
                    variables = []

                # Check if template already exists
                existing_template = Template.query.filter_by(name=metadata['name']).first()
                if existing_template:
                    messages.append(f"Template '{metadata['name']}' already exists - creating with unique name")
                    metadata['name'] = f"{metadata['name']} (Imported)"

                # Create template record (only fields that exist on Template model)
                template = Template(
                    name=metadata['name'],
                    description=metadata.get('description') or '',
                    category=metadata.get('category') or 'generic',
                    template_type=metadata.get('template_type', 'main'),
                    template_html=html_content,
                    variables=variables,
                    created_by_id=user_id
                )
                db.session.add(template)
                db.session.flush()
                template_id = template.id

                # Save preview image to storage/template_previews/<id>.png
                if 'preview.png' in zf.namelist():
                    preview_data = zf.read('preview.png')
                    previews_dir = self.storage_base / 'template_previews'
                    previews_dir.mkdir(parents=True, exist_ok=True)
                    preview_path = previews_dir / f"{template_id}.png"
                    preview_path.write_bytes(preview_data)
                    template.preview_image = f"template_previews/{template_id}.png"

                # Import assets into storage/templates/<id>/assets/ (filesystem only)
                assets_dir = self.storage_base / 'templates' / str(template_id) / 'assets'
                assets_dir.mkdir(parents=True, exist_ok=True)
                assets_imported = 0
                for file_path in zf.namelist():
                    if file_path.startswith('assets/') and not file_path.endswith('/'):
                        try:
                            asset_data = zf.read(file_path)
                            original_filename = Path(file_path).name
                            safe_name = secure_filename(original_filename)
                            out_path = assets_dir / safe_name
                            out_path.write_bytes(asset_data)
                            assets_imported += 1
                        except Exception as e:
                            logger.warning(f"Failed to import asset {file_path}: {e}")
                            messages.append(f"Warning: Failed to import asset {file_path}")

                db.session.commit()
                messages.append(f"Successfully imported template '{template.name}'")
                if assets_imported > 0:
                    messages.append(f"Imported {assets_imported} asset files")
                return True, messages, template_id
        
        except Exception as e:
            logger.exception(f"Failed to import template package: {e}")
            if template_id:
                # Cleanup on failure
                try:
                    template = Template.query.get(template_id)
                    if template:
                        db.session.delete(template)
                        db.session.commit()
                except:
                    pass
            
            return False, [f"Import failed: {str(e)}"], None

    def _import_multi_template(self, zip_path: Path, user_id: int = None) -> Tuple[bool, List[str], Optional[List[int]]]:
        """Import a multi-template package"""
        from shared.database import db, Template, Asset
        
        messages = []
        template_ids = []
        
        try:
            # Validate package first
            is_valid, errors, metadata = self.validate_package(zip_path)
            if not is_valid:
                return False, errors, None

            with zipfile.ZipFile(zip_path, 'r') as zf:
                templates_data = metadata.get('templates', {})
                
                for template_dir, template_metadata in templates_data.items():
                    try:
                        # Read template files
                        template_json_path = f"{template_dir}/template.json"
                        template_html_path = f"{template_dir}/template.html"
                        
                        metadata_content = zf.read(template_json_path)
                        template_meta = json.loads(metadata_content.decode('utf-8'))
                        html_content = zf.read(template_html_path).decode('utf-8')
                        
                        # Check if template already exists
                        existing_template = Template.query.filter_by(name=template_meta['name']).first()
                        if existing_template:
                            messages.append(f"Template '{template_meta['name']}' already exists - creating with unique name")
                            template_meta['name'] = f"{template_meta['name']} (Imported)"
                        
                        # Create template record
                        template = Template(
                            name=template_meta['name'],
                            description=template_meta.get('description', ''),
                            category=template_meta.get('category', 'imported'),
                            template_type=template_meta.get('template_type', 'phishing'),
                            template_html=html_content,
                            variables=template_meta.get('variables', {}),
                            author=template_meta.get('author', 'Imported'),
                            version=template_meta.get('version', '1.0'),
                            tags=template_meta.get('tags', []),
                            created_by_id=user_id
                        )
                        
                        # Read preview image if exists
                        preview_path_in_zip = f"{template_dir}/preview.png"
                        if preview_path_in_zip in zf.namelist():
                            preview_data = zf.read(preview_path_in_zip)
                            preview_path = self.assets_dir / f"preview_{hashlib.md5(preview_data).hexdigest()}.png"
                            with open(preview_path, 'wb') as f:
                                f.write(preview_data)
                            template.preview_image = str(preview_path.relative_to(self.storage_base))
                        
                        db.session.add(template)
                        db.session.flush()  # Get template ID
                        template_id = template.id
                        template_ids.append(template_id)
                        
                        # Import assets for this template
                        assets_imported = 0
                        template_assets_prefix = f"{template_dir}/assets/"
                        
                        for file_path in zf.namelist():
                            if file_path.startswith(template_assets_prefix) and not file_path.endswith('/'):
                                try:
                                    # Read asset file
                                    asset_data = zf.read(file_path)
                                    original_filename = Path(file_path).name
                                    secure_name = secure_filename(original_filename)
                                    
                                    # Generate unique filename
                                    file_hash = hashlib.md5(asset_data).hexdigest()
                                    file_ext = Path(original_filename).suffix
                                    unique_filename = f"{template_id}_{file_hash}{file_ext}"
                                    
                                    # Save asset file
                                    asset_path = self.assets_dir / unique_filename
                                    with open(asset_path, 'wb') as f:
                                        f.write(asset_data)
                                    
                                    # Create asset record
                                    mime_type, _ = mimetypes.guess_type(original_filename)
                                    asset = Asset(
                                        filename=unique_filename,
                                        original_filename=original_filename,
                                        file_path=str(asset_path),
                                        file_hash=file_hash,
                                        mime_type=mime_type or 'application/octet-stream',
                                        file_size=len(asset_data),
                                        template_id=template_id,
                                        uploaded_by_id=user_id
                                    )
                                    
                                    db.session.add(asset)
                                    assets_imported += 1
                                    
                                except Exception as e:
                                    logger.warning(f"Failed to import asset {file_path}: {e}")
                                    messages.append(f"Warning: Failed to import asset {file_path}")
                        
                        messages.append(f"Successfully imported template '{template.name}' from {template_dir}/")
                        if assets_imported > 0:
                            messages.append(f"  └ Imported {assets_imported} asset files")
                            
                    except Exception as e:
                        logger.exception(f"Failed to import template from {template_dir}: {e}")
                        messages.append(f"Failed to import template from {template_dir}: {str(e)}")
                        continue
                
                db.session.commit()
                
                if template_ids:
                    messages.append(f"Successfully imported {len(template_ids)} templates from package")
                    return True, messages, template_ids
                else:
                    return False, ["No templates were successfully imported"], None
        
        except Exception as e:
            logger.exception(f"Failed to import multi-template package: {e}")
            # Cleanup on failure
            for template_id in template_ids:
                try:
                    template = Template.query.get(template_id)
                    if template:
                        db.session.delete(template)
                        db.session.commit()
                except:
                    pass
            
            return False, [f"Import failed: {str(e)}"], None
    
    def export_template(self, template_id: int, output_path: Path = None) -> Tuple[bool, str, Optional[Path]]:
        """
        Export a template as a zip package
        
        Returns:
            (success, message, zip_path)
        """
        from shared.database import Template
        
        try:
            template = Template.query.get(template_id)
            if not template:
                return False, "Template not found", None
            
            # Generate output path if not provided
            if output_path is None:
                safe_name = secure_filename(template.name)
                output_path = self.temp_dir / f"{safe_name}_{template_id}.zip"
            
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Create metadata (Template has no version/author/tags; use safe defaults)
                metadata = {
                    'name': template.name,
                    'description': template.description,
                    'category': template.category,
                    'template_type': template.template_type,
                    'author': template.created_by.username if template.created_by else None,
                    'version': '1.0',
                    'tags': [],
                    'variables': template.variables if template.variables is not None else [],
                    'created_at': template.created_at.isoformat() if template.created_at else None,
                    'exported_at': datetime.utcnow().isoformat(),
                    'exported_by': 'Reel v2'
                }
                
                # Add metadata
                zf.writestr('template.json', json.dumps(metadata, indent=2))
                
                # Add HTML template
                zf.writestr('template.html', template.template_html)
                
                # Add preview image if exists
                if template.preview_image:
                    preview_path = self.storage_base / template.preview_image
                    if preview_path.exists():
                        zf.write(preview_path, 'preview.png')
                
                # Add assets from filesystem (template assets live in storage/templates/<id>/assets/)
                assets_dir = self.storage_base / "templates" / str(template_id) / "assets"
                assets_added = 0
                if assets_dir.exists():
                    for file_path in assets_dir.iterdir():
                        if file_path.is_file():
                            archive_path = f"assets/{file_path.name}"
                            zf.write(file_path, archive_path)
                            assets_added += 1
                
                message = f"Successfully exported template '{template.name}'"
                if assets_added > 0:
                    message += f" with {assets_added} assets"
                
                return True, message, output_path
        
        except Exception as e:
            logger.exception(f"Failed to export template {template_id}: {e}")
            return False, f"Export failed: {str(e)}", None
    
    def list_package_contents(self, zip_path: Path) -> Dict[str, Any]:
        """List contents of a template package for preview (single or multi-template)"""
        try:
            # First validate to get package type and structure
            is_valid, errors, metadata = self.validate_package(zip_path)
            
            contents = {
                'metadata': metadata,
                'files': [],
                'assets': [],
                'total_size': 0,
                'file_count': 0,
                'package_type': metadata.get('package_type') if metadata else 'unknown',
                'template_count': 1 if metadata and metadata.get('package_type') == 'single' else metadata.get('template_count', 0) if metadata else 0
            }
            
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # List all files
                for file_info in zf.filelist:
                    if file_info.filename.endswith('/'):
                        continue  # Skip directories
                    
                    file_data = {
                        'path': file_info.filename,
                        'size': file_info.file_size,
                        'compressed_size': file_info.compress_size,
                        'type': 'file'
                    }
                    
                    # Determine if this is an asset based on package type
                    is_asset = False
                    if contents['package_type'] == 'single':
                        is_asset = file_info.filename.startswith('assets/')
                    elif contents['package_type'] == 'multi':
                        is_asset = '/assets/' in file_info.filename
                    
                    file_data['is_asset'] = is_asset
                    
                    if is_asset:
                        contents['assets'].append(file_data)
                    else:
                        contents['files'].append(file_data)
                    
                    contents['total_size'] += file_info.file_size
                    contents['file_count'] += 1
            
            return contents
            
        except Exception as e:
            logger.exception(f"Failed to list package contents: {e}")
            return {'error': str(e)}

    def cleanup_temp_files(self, older_than_hours: int = 24):
        """Clean up temporary files older than specified hours"""
        try:
            import time
            cutoff_time = time.time() - (older_than_hours * 3600)
            
            for file_path in self.temp_dir.glob('*'):
                if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                    file_path.unlink()
                    logger.info(f"Cleaned up temporary file: {file_path}")
                    
        except Exception as e:
            logger.exception(f"Error during temp file cleanup: {e}")