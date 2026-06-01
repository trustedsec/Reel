"""
GraphSpy plugin - generates Azure device codes via GraphSpy API
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.config import Config
import logging
import requests

logger = logging.getLogger(__name__)


class GraphSpyPlugin(BasePlugin):
    """
    Plugin for generating Azure device codes via GraphSpy API.
    
    Generates Azure AD device codes for OAuth 2.0 device code flow authentication.
    These codes can be delivered via phone calls (using AWS Connect Dialer) or
    other channels. Supports SSML formatting for speech synthesis.
    
    Use cases:
    - Generate device codes for MFA/authentication flows
    - Create codes for phone-based delivery
    - Integrate with AWS Connect for voice delivery
    - Multi-factor authentication campaigns
    
    Requirements:
    - GraphSpy API endpoint configured
    - Azure AD application/client ID (optional)
    - Network access to GraphSpy service
    
    Example: Generate device code, format for speech, then deliver via AWS Connect
    dialer plugin in the same workflow.
    """
    
    @property
    def plugin_type(self) -> str:
        return "graphspy"
    
    @property
    def display_name(self) -> str:
        return "Generate Device Code (GraphSpy)"
    
    @property
    def description(self) -> str:
        return "Generate Azure AD device codes via GraphSpy API for OAuth 2.0 device code flow. Supports SSML formatting for speech synthesis. Use with AWS Connect Dialer to deliver codes via phone calls. Requires GraphSpy API endpoint."
    
    @property
    def plugin_category(self) -> str:
        return "data_transform"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "graphspy_url": {
                    "type": "string",
                    "title": "GraphSpy API URL",
                    "description": "GraphSpy API endpoint URL",
                    "help": "Full URL to the GraphSpy API endpoint that generates device codes. If not provided, falls back to GRAPHSPY_URL configuration setting. Example: http://vigorous_johnson.orb.local/api/generate_device_code",
                    "placeholder": "http://graphspy.example.com/api/generate_device_code"
                },
                "graphspy_client_id": {
                    "type": "string",
                    "title": "Azure Client ID",
                    "description": "Azure AD application client ID (optional)",
                    "help": "Azure AD application (client) ID for the device code flow. Optional - GraphSpy may use default if not provided. Format: GUID like '12345678-1234-1234-1234-123456789012'.",
                    "placeholder": "12345678-1234-1234-1234-123456789012"
                },
                "graphspy_tenant": {
                    "type": "string",
                    "title": "Azure Tenant",
                    "description": "Azure AD tenant identifier",
                    "help": "Azure AD tenant for authentication. Use 'common' for multi-tenant, 'organizations' for work/school accounts, 'consumers' for personal Microsoft accounts, or specific tenant ID.",
                    "placeholder": "common, organizations, consumers, tenant-id",
                    "default": "common"
                },
                "resource": {
                    "type": "string",
                    "title": "Azure Resource",
                    "description": "Azure resource/audience for the token",
                    "help": "The Azure resource (audience) that the device code will be used to access. Typically the Microsoft Graph API endpoint. Default: https://graph.microsoft.com",
                    "placeholder": "https://graph.microsoft.com",
                    "default": "https://graph.microsoft.com"
                },
                "scope": {
                    "type": "string",
                    "title": "Azure Scope",
                    "description": "OAuth 2.0 scope for the token",
                    "help": "OAuth 2.0 scope(s) requested for the access token. Use .default suffix for application permissions. Default: https://graph.microsoft.com/.default",
                    "placeholder": "https://graph.microsoft.com/.default",
                    "default": "https://graph.microsoft.com/.default"
                },
                "output_variable": {
                    "type": "string",
                    "title": "Output Variable Name",
                    "description": "Context variable name to store the device code",
                    "help": "Name of the context variable where the generated device code will be stored. This can be used by subsequent plugins (e.g., AWS Connect Dialer). If format_for_speech is enabled, also stores raw code in '{variable}_raw'.",
                    "placeholder": "device_code, auth_code, verification_code",
                    "default": "device_code"
                },
                "format_for_speech": {
                    "type": "boolean",
                    "title": "Format for Speech",
                    "description": "Format device code with SSML pauses for speech synthesis",
                    "help": "When enabled, formats the device code with SSML <break time='500ms'/> tags between each character for clear speech synthesis. Use this when delivering codes via phone calls (AWS Connect). The formatted code is stored in the output variable, and the raw code is stored in '{output_variable}_raw'.",
                    "default": False
                }
            },
            "required": []
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute GraphSpy device code generation"""
        try:
            # Get device code from GraphSpy
            device_code = self._get_device_code_from_graphspy(config)
            
            if not device_code:
                logger.error("Failed to get device code from GraphSpy")
                context['_graphspy_result'] = {
                    'success': False,
                    'error': 'Failed to get device code from GraphSpy',
                    'device_code': None
                }
                return self.on_error(Exception("Failed to get device code from GraphSpy"), context, config)
            
            # Format for speech if requested
            output_var = config.get('output_variable', 'device_code')
            if config.get('format_for_speech', False):
                formatted_code = self._format_code_for_speech(device_code)
                context[output_var] = formatted_code
                context[f'{output_var}_raw'] = device_code  # Store raw code too
            else:
                context[output_var] = device_code
            
            # Store result metadata
            context['_graphspy_result'] = {
                'success': True,
                'device_code': device_code,
                'error': None
            }
            
            logger.info(f"Successfully generated device code: {device_code[:4]}...")
            
        except Exception as e:
            logger.error(f"GraphSpy execution failed: {e}", exc_info=True)
            context['_graphspy_result'] = {
                'success': False,
                'error': str(e),
                'device_code': None
            }
            return self.on_error(e, context, config)
        
        return context
    
    def _get_device_code_from_graphspy(
        self, 
        config: Dict[str, Any]
    ) -> Optional[str]:
        """
        Get device code from GraphSpy API
        
        Args:
            config: Plugin configuration
            
        Returns:
            Device code string or None if failed
        """
        try:
            # Get GraphSpy URL from config or fallback to shared config
            graphspy_url = config.get('graphspy_url')
            if not graphspy_url:
                # Try to get from shared config
                try:
                    graphspy_url = Config.get_setting('GRAPHSPY_URL', 'http://localhost:5000')
                except:
                    graphspy_url = 'http://localhost:5000'
            
            client_id = config.get('graphspy_client_id', '')
            tenant = config.get('graphspy_tenant', 'common')
            resource = config.get('resource', 'https://graph.microsoft.com')
            scope = config.get('scope', 'https://graph.microsoft.com/.default')
            
            params = {
                'client_id': client_id or '',
                'resource': resource,
                'scope': scope,
                'tenant': tenant
            }
            
            # Remove empty values
            params = {k: v for k, v in params.items() if v}
            
            response = requests.post(
                graphspy_url,
                data=params,
                timeout=10
            )
            
            if response.status_code == 200:
                code = response.text.strip()
                logger.debug(f"Got device code from GraphSpy: {code[:4]}...")
                return code
            else:
                logger.error(f"GraphSpy API error: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"GraphSpy request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting device code from GraphSpy: {e}")
            return None
    
    def _format_code_for_speech(self, code: str) -> str:
        """
        Format device code with SSML pauses between each character
        
        Args:
            code: Device code string
            
        Returns:
            SSML-formatted string with pauses
        """
        # Remove hyphens and convert to uppercase
        code = code.upper().replace('-', '').replace(' ', '')
        
        # Add 500ms pause between each character
        chars_with_pauses = ''.join([
            f'{char}<break time="500ms"/>' 
            for char in code
        ])
        
        return chars_with_pauses
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate GraphSpy configuration"""
        errors = []
        # No required fields - all are optional with defaults
        return errors if errors else None
