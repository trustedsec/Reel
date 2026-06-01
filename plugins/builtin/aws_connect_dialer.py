"""
AWS Connect dialer plugin - makes outbound calls via AWS Connect
"""
from typing import Dict, Any, Optional, List, Tuple
from ..base import BasePlugin
from shared.workflow_variables import get_variable_value
from shared.database import db, CallJob
from datetime import datetime
import logging
import time

logger = logging.getLogger(__name__)

# Try to import boto3, but handle gracefully if not available
try:
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    logger.warning("boto3 not available. AWS Connect dialer plugin will not work.")


class AWSConnectDialerPlugin(BasePlugin):
    """
    Plugin for making outbound voice calls via AWS Connect.
    
    Initiates automated voice calls through AWS Connect service, delivering messages
    or device codes to target phone numbers. Supports SSML-formatted device codes
    for speech synthesis and can integrate with GraphSpy plugin for Azure device
    code generation.
    
    Use cases:
    - Deliver device codes via automated phone calls
    - Send voice notifications to targets
    - Multi-factor authentication delivery
    - Automated phone-based campaigns
    
    Requirements:
    - AWS Connect instance configured
    - Contact flow created in AWS Connect
    - boto3 library installed
    - AWS credentials (access key/secret or IAM role)
    - Phone number verified in AWS Connect
    
    Example: Call target phone number, deliver device code from GraphSpy plugin
    using SSML formatting for clear speech synthesis.
    """
    
    @property
    def plugin_type(self) -> str:
        return "aws_connect_dialer"
    
    @property
    def display_name(self) -> str:
        return "AWS Connect Dialer"
    
    @property
    def description(self) -> str:
        return "Make outbound voice calls via AWS Connect to deliver messages or device codes. Supports SSML-formatted speech, integrates with GraphSpy for device codes, and uses AWS Connect contact flows for call handling. Requires AWS Connect instance, contact flow, and boto3 library."
    
    @property
    def plugin_category(self) -> str:
        return "sending"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "aws_region": {
                    "type": "string",
                    "title": "AWS Region",
                    "description": "AWS region where your Connect instance is located",
                    "help": "The AWS region code where your Connect instance is deployed. Common regions: us-east-1, us-west-2, eu-west-1, ap-southeast-1. Find your region in AWS Connect console.",
                    "placeholder": "us-west-2, us-east-1, eu-west-1",
                    "default": "us-west-2"
                },
                "aws_access_key_id": {
                    "type": "string",
                    "title": "AWS Access Key ID",
                    "description": "AWS access key ID for authentication (optional)",
                    "help": "AWS access key ID for programmatic access. If not provided, plugin will use AWS_ACCESS_KEY_ID environment variable or IAM role credentials. Recommended: Use IAM role or environment variables for security.",
                    "placeholder": "AKIAIOSFODNN7EXAMPLE"
                },
                "aws_secret_access_key": {
                    "type": "string",
                    "title": "AWS Secret Access Key",
                    "description": "AWS secret access key for authentication (optional)",
                    "help": "AWS secret access key corresponding to the access key ID. If not provided, plugin will use AWS_SECRET_ACCESS_KEY environment variable or IAM role credentials. Recommended: Use IAM role or environment variables for security.",
                    "placeholder": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
                },
                "instance_id": {
                    "type": "string",
                    "title": "Connect Instance ID",
                    "description": "AWS Connect instance ID",
                    "help": "The unique identifier for your AWS Connect instance. Find this in the AWS Connect console under Instance settings. Format: UUID like 'fc2f0892-939f-4c7a-a2ed-755cc53fb1d7'.",
                    "placeholder": "fc2f0892-939f-4c7a-a2ed-755cc53fb1d7"
                },
                "contact_flow_id": {
                    "type": "string",
                    "title": "Contact Flow ID",
                    "description": "AWS Connect contact flow ID to use for the call",
                    "help": "The contact flow ID that defines what happens during the call (what the target hears, what data is passed, etc.). Create or select a contact flow in AWS Connect console. Format: UUID like '346a285b-695d-4996-a555-39f26b26ac83'.",
                    "placeholder": "346a285b-695d-4996-a555-39f26b26ac83"
                },
                "source_phone": {
                    "type": "string",
                    "title": "Source Phone Number",
                    "description": "Phone number to call from (E.164 format)",
                    "help": "The phone number that will appear as the caller ID. Must be in E.164 format (e.g., +442046238638, +12025551234). This number must be verified/claimed in your AWS Connect instance.",
                    "placeholder": "+442046238638, +12025551234"
                },
                "device_code_field": {
                    "type": "string",
                    "title": "Device Code Field Name",
                    "description": "Context variable name containing device code (optional)",
                    "help": "Name of the context variable containing the device code to deliver. Typically set by GraphSpy plugin. If device code is SSML-formatted (contains <break time=), it will be used as-is. Otherwise, it will be formatted for speech. Leave empty to make call without device code.",
                    "placeholder": "device_code, graphspy_code, auth_code",
                    "default": "device_code"
                },
                "target_phone_field": {
                    "type": "string",
                    "title": "Target Phone Field Name",
                    "description": "Context variable name for target phone number",
                    "help": "Name of the context variable containing the target's phone number. Supports nested paths (e.g., 'target.phone', 'user.contact.phone'). Phone should be in E.164 format. Default: 'phone'.",
                    "placeholder": "phone, target.phone, user.contact.phone",
                    "default": "phone"
                },
                "target_name_field": {
                    "type": "string",
                    "title": "Target Name Field Name",
                    "description": "Context variable name for target name",
                    "help": "Name of the context variable containing the target's name. Supports nested paths (e.g., 'target.name', 'user.full_name'). Used in contact flow attributes. Default: 'name'.",
                    "placeholder": "name, target.name, user.full_name",
                    "default": "name"
                },
                "target_company_field": {
                    "type": "string",
                    "title": "Target Company Field Name",
                    "description": "Context variable name for target company",
                    "help": "Name of the context variable containing the target's company name. Supports nested paths (e.g., 'target.company', 'user.organization'). Used in contact flow attributes. Default: 'company'.",
                    "placeholder": "company, target.company, user.organization",
                    "default": "company"
                }
            },
            "required": ["instance_id", "contact_flow_id", "source_phone"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute AWS Connect dialer plugin"""
        if not BOTO3_AVAILABLE:
            logger.error("boto3 is not available. Please install it: pip install boto3")
            context['_call_result'] = {
                'success': False,
                'error': 'boto3 not available',
                'device_code': None,
                'contact_id': None
            }
            return self.on_error(Exception("boto3 not available"), context, config)
        
        try:
            # Get target information from context
            phone_field = config.get('target_phone_field', 'phone')
            name_field = config.get('target_name_field', 'name')
            company_field = config.get('target_company_field', 'company')
            
            # Get values using nested path support
            target_phone = get_variable_value(context, phone_field)
            target_name = get_variable_value(context, name_field, '')
            target_company = get_variable_value(context, company_field, '')
            
            if not target_phone:
                raise ValueError(f"Target phone number not found in context at '{phone_field}'")
            
            # Validate phone number format (basic E.164 check)
            if not target_phone.startswith('+'):
                logger.warning(f"Phone number '{target_phone}' does not start with '+'. Expected E.164 format.")
            
            # Get device code from context (optional - from GraphSpy plugin or other source)
            device_code_field = config.get('device_code_field', 'device_code')
            device_code = context.get(device_code_field)
            spoken_code = None
            raw_code = None
            
            if device_code:
                # Check if already formatted for speech (has SSML breaks)
                if '<break time=' in device_code:
                    spoken_code = device_code
                    # Extract raw code if available
                    raw_code = context.get(f'{device_code_field}_raw', device_code.replace('<break time="500ms"/>', '').replace('<break time=\'500ms\'/>', ''))
                else:
                    # Format code for speech
                    spoken_code = self._format_code_for_speech(device_code)
                    raw_code = device_code
                logger.debug(f"Device code found: {raw_code[:4] if raw_code else 'N/A'}...")
            else:
                logger.debug(f"No device code found in context at '{device_code_field}'. Call will proceed without device code.")
            
            # Make outbound call
            success, contact_id = self._make_outbound_call(
                target_phone=target_phone,
                target_name=target_name,
                target_company=target_company,
                device_code=spoken_code,
                config=config
            )
            
            # Get campaign_id from context
            campaign_id = context.get('campaign', {}).get('id')
            workflow_id = context.get('_workflow_id')
            
            # Create CallJob record
            try:
                if campaign_id:
                    call_job = CallJob(
                        workflow_id=workflow_id,
                        campaign_id=campaign_id,
                        target_phone=target_phone,
                        target_data={
                            'name': target_name,
                            'company': target_company
                        },
                        call_content={
                            'device_code': raw_code,
                            'message': 'Device code delivered' if raw_code else 'Call initiated'
                        },
                        status='completed' if success else 'failed',
                        contact_id=contact_id if success else None,
                        completed_at=datetime.utcnow() if success else datetime.utcnow(),
                        error_message=None if success else 'Call failed',
                        retry_count=0
                    )
                    db.session.add(call_job)
                    db.session.commit()
                    logger.info(f"Call job created: {call_job.id} for {target_phone}")
                else:
                    logger.warning(f"No campaign_id in context, skipping CallJob creation for {target_phone}")
            except Exception as db_error:
                # Log database error but don't fail the plugin execution
                logger.error(f"Failed to create CallJob record: {db_error}", exc_info=True)
                db.session.rollback()
            
            # Store result in context
            context['_call_result'] = {
                'success': success,
                'device_code': raw_code,
                'contact_id': contact_id,
                'error': None if success else 'Call failed'
            }
            
            # Preserve device_code in context (already there from GraphSpy plugin)
            if contact_id:
                context['contact_id'] = contact_id
            
            if success:
                if raw_code:
                    logger.info(f"Successfully called {target_phone} with device code {raw_code[:4]}...")
                else:
                    logger.info(f"Successfully called {target_phone}")
            else:
                logger.error(f"Failed to call {target_phone}")
            
        except Exception as e:
            logger.error(f"AWS Connect dialer execution failed: {e}", exc_info=True)
            
            # Create CallJob record for exception case
            try:
                campaign_id = context.get('campaign', {}).get('id')
                workflow_id = context.get('_workflow_id')
                target_phone = get_variable_value(context, config.get('target_phone_field', 'phone'), '')
                
                if campaign_id and target_phone:
                    call_job = CallJob(
                        workflow_id=workflow_id,
                        campaign_id=campaign_id,
                        target_phone=target_phone,
                        target_data={
                            'name': get_variable_value(context, config.get('target_name_field', 'name'), ''),
                            'company': get_variable_value(context, config.get('target_company_field', 'company'), '')
                        },
                        call_content={},
                        status='failed',
                        contact_id=None,
                        completed_at=datetime.utcnow(),
                        error_message=str(e),
                        retry_count=0
                    )
                    db.session.add(call_job)
                    db.session.commit()
                    logger.info(f"Call job created (failed): {call_job.id} for {target_phone}")
            except Exception as db_error:
                logger.error(f"Failed to create CallJob record for exception: {db_error}", exc_info=True)
                db.session.rollback()
            
            context['_call_result'] = {
                'success': False,
                'error': str(e),
                'device_code': None,
                'contact_id': None
            }
            return self.on_error(e, context, config)
        
        return context
    
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
    
    def _make_outbound_call(
        self,
        target_phone: str,
        target_name: str,
        target_company: str,
        device_code: Optional[str],
        config: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Make outbound call via AWS Connect
        
        Args:
            target_phone: Destination phone number (E.164 format)
            target_name: Target name
            target_company: Target company
            device_code: SSML-formatted device code (optional)
            config: Plugin configuration
            
        Returns:
            Tuple of (success: bool, contact_id: Optional[str])
        """
        try:
            # Get AWS configuration
            aws_region = config.get('aws_region', 'us-west-2')
            instance_id = config['instance_id']
            contact_flow_id = config['contact_flow_id']
            source_phone = config['source_phone']
            
            # Create boto3 client with credentials if provided
            aws_access_key_id = config.get('aws_access_key_id')
            aws_secret_access_key = config.get('aws_secret_access_key')
            
            if aws_access_key_id and aws_secret_access_key:
                # Use explicit credentials
                session = boto3.Session(
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    region_name=aws_region
                )
                connect_client = session.client('connect', region_name=aws_region)
            else:
                # Use default credentials (env vars, IAM role, etc.)
                connect_client = boto3.client('connect', region_name=aws_region)
            
            # Prepare attributes for the contact flow
            attributes = {
                'full_name': target_name or '',
                'company_name': target_company or ''
            }
            
            # Only add device_code if it exists
            if device_code:
                attributes['device_code'] = device_code
            
            # Make the outbound call
            response = connect_client.start_outbound_voice_contact(
                DestinationPhoneNumber=target_phone,
                ContactFlowId=contact_flow_id,
                InstanceId=instance_id,
                SourcePhoneNumber=source_phone,
                Attributes=attributes
            )
            
            contact_id = response.get('ContactId')
            logger.info(f"Outbound call initiated. Contact ID: {contact_id}")
            
            return True, contact_id
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"AWS Connect API error ({error_code}): {error_message}")
            return False, None
        except BotoCoreError as e:
            logger.error(f"AWS SDK error: {e}")
            return False, None
        except Exception as e:
            logger.error(f"Error making outbound call: {e}", exc_info=True)
            return False, None
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate AWS Connect dialer configuration"""
        errors = []
        
        if not BOTO3_AVAILABLE:
            errors.append("boto3 is not installed. Please install it: pip install boto3")
        
        if not config.get('instance_id'):
            errors.append("instance_id is required")
        
        if not config.get('contact_flow_id'):
            errors.append("contact_flow_id is required")
        
        if not config.get('source_phone'):
            errors.append("source_phone is required")
        elif not config['source_phone'].startswith('+'):
            errors.append("source_phone must be in E.164 format (start with +)")
        
        return errors if errors else None
