"""
SMTP email sender plugin - sends emails via SMTP
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.workflow_variables import interpolate_string
import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

logger = logging.getLogger(__name__)

class SMTPEmailSenderPlugin(BasePlugin):
    """
    Plugin for sending emails via SMTP server.

    Sends HTML and/or plain text emails through any SMTP server with support
    for TLS encryption, authentication, and variable interpolation in email
    content. Supports both HTML and plain text body formats.

    When no SMTP host is configured, the plugin resolves the recipient's MX
    record and delivers directly to the target mail server on port 25.

    Use cases:
    - Send phishing emails to targets
    - Deliver notifications via email
    - Send personalized emails with dynamic content
    - Multi-recipient email campaigns
    - Email-based authentication flows
    - Direct MX delivery for email spoofing workflows

    Requirements:
    - SMTP server hostname and port (or leave blank for direct MX delivery)
    - SMTP credentials (username/password) if authentication required
    - From and To email addresses

    Example: Send personalized email to {{target.email}} with subject
    "Action Required" and HTML body containing {{target.name}}.
    """

    @property
    def plugin_type(self) -> str:
        return "smtp_sender"

    @property
    def display_name(self) -> str:
        return "SMTP Email Sender"

    @property
    def description(self) -> str:
        return "Send emails via SMTP server with HTML and/or plain text support. Includes TLS encryption, authentication, and variable interpolation. Use for phishing emails, notifications, personalized campaigns, and email-based authentication flows."

    @property
    def plugin_category(self) -> str:
        return "sending"

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "smtp_host": {
                    "type": "string",
                    "title": "SMTP Server Hostname",
                    "description": "Hostname or IP address of SMTP server",
                    "help": "SMTP relay server hostname. Leave blank for direct delivery — the plugin will "
                            "resolve the recipient's MX record and connect directly on port 25. "
                            "Direct delivery enables email spoofing without a relay. "
                            "Examples: smtp.gmail.com, smtp.office365.com, or leave blank for direct MX.",
                    "placeholder": "smtp.gmail.com, smtp.office365.com, mail.example.com"
                },
                "smtp_port": {
                    "type": "integer",
                    "title": "SMTP Port",
                    "description": "SMTP server port number",
                    "help": "SMTP port. Default: 587 (relay with TLS) or 25 (direct MX delivery when smtp_host is blank). "
                            "Common ports: 587 (STARTTLS), 465 (SSL), 25 (direct/unencrypted).",
                    "placeholder": "587, 465, 25",
                    "default": 587
                },
                "smtp_username": {
                    "type": "string",
                    "title": "SMTP Username",
                    "description": "Username for SMTP authentication",
                    "help": "SMTP authentication username. Usually the email address or account username. Required if SMTP server requires authentication.",
                    "placeholder": "user@example.com, username"
                },
                "smtp_password": {
                    "type": "string",
                    "title": "SMTP Password",
                    "description": "Password for SMTP authentication",
                    "help": "SMTP authentication password. For Gmail, use an app-specific password. Required if SMTP server requires authentication.",
                    "placeholder": "your-password, app-specific-password"
                },
                "use_tls": {
                    "type": "boolean",
                    "title": "Use TLS Encryption",
                    "description": "Enable TLS/STARTTLS encryption for SMTP connection",
                    "help": "Enable STARTTLS encryption. Default: true for relay mode, false for direct MX delivery. "
                            "Most MX servers on port 25 do not require TLS.",
                    "default": True
                },
                "from_email": {
                    "type": "string",
                    "title": "From Email Address",
                    "format": "email",
                    "description": "Sender email address",
                    "help": "Email address that will appear as the sender. Must be a valid email format. Supports variable interpolation: {{sender.email}} or {{campaign.from_email}}.",
                    "placeholder": "noreply@example.com, {{sender.email}}"
                },
                "from_name": {
                    "type": "string",
                    "title": "From Name",
                    "description": "Display name for sender",
                    "help": "Optional display name for the sender. If provided, email will show as 'Name <email@example.com>'. Supports variable interpolation.",
                    "placeholder": "Support Team, {{company.name}} Support"
                },
                "to_email": {
                    "type": "string",
                    "title": "To Email Address",
                    "format": "email",
                    "description": "Recipient email address",
                    "help": "Recipient email address. Supports variable interpolation from context: {{target.email}}, {{user.email}}, etc. Can also be a direct email address.",
                    "placeholder": "{{target.email}}, user@example.com"
                },
                "subject": {
                    "type": "string",
                    "title": "Email Subject",
                    "description": "Email subject line",
                    "help": "Email subject line. Supports variable interpolation: {{target.name}}, {{campaign.name}}, etc. Example: 'Action Required: {{target.name}}' or 'Your account needs attention'.",
                    "placeholder": "Action Required, Your account needs attention, {{campaign.name}}"
                },
                "body_html": {
                    "type": "string",
                    "title": "HTML Body",
                    "description": "Email HTML body content",
                    "help": "HTML content for email body. Supports variable interpolation and can include HTML tags. If both HTML and text are provided, email clients will show HTML version. Example: <h1>Hello {{target.name}}</h1><p>Your email: {{target.email}}</p>",
                    "placeholder": "<html><body><h1>{{target.name}}</h1></body></html>"
                },
                "body_text": {
                    "type": "string",
                    "title": "Plain Text Body",
                    "description": "Email plain text body content",
                    "help": "Plain text content for email body. Supports variable interpolation. Used as fallback for email clients that don't support HTML. If only text is provided, email will be text-only. Example: Hello {{target.name}}, your email is {{target.email}}.",
                    "placeholder": "Hello {{target.name}}, your email is {{target.email}}"
                },
                "ehlo_hostname": {
                    "type": "string",
                    "title": "EHLO Hostname",
                    "description": "Hostname to identify as during SMTP handshake",
                    "help": "Controls the HELO/EHLO greeting sent to the mail server. "
                            "If blank, Python's default (local hostname) is used. "
                            "Example: mail.example.com",
                    "placeholder": "mail.example.com"
                },
                "envelope_from": {
                    "type": "string",
                    "title": "Envelope From (MAIL FROM)",
                    "description": "SMTP envelope sender address (overrides From header for routing)",
                    "help": "Controls the MAIL FROM in the SMTP envelope, separate from the From header. "
                            "Useful for SPF alignment or bounce routing. If blank, uses the From email address. "
                            "Supports variable interpolation.",
                    "placeholder": "bounce@example.com"
                },
                "custom_headers": {
                    "type": "object",
                    "title": "Custom Email Headers",
                    "description": "Additional email headers as key-value pairs",
                    "help": "Add custom headers to the email. Common headers: Reply-To, X-Mailer, "
                            "Message-ID, X-Priority. Values support variable interpolation. "
                            "Example: {\"Reply-To\": \"reply@example.com\", \"X-Mailer\": \"CustomMailer/1.0\"}",
                    "placeholder": "{\"Reply-To\": \"reply@example.com\"}"
                },
                "attachments": {
                    "type": "array",
                    "title": "Attachments",
                    "description": "File attachments to include in the email",
                    "help": "List of file attachments. Each item needs a 'path' (absolute file path on server) "
                            "and optional 'filename' (display name) and 'mime_type'. "
                            "Paths support variable interpolation. "
                            "Example: [{\"path\": \"/tmp/report.pdf\", \"filename\": \"report.pdf\"}]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "title": "File Path"},
                            "filename": {"type": "string", "title": "Display Filename"},
                            "mime_type": {"type": "string", "title": "MIME Type", "default": "application/octet-stream"}
                        },
                        "required": ["path"]
                    }
                }
            },
            "required": ["from_email", "to_email", "subject"]
        }

    def _resolve_mx(self, domain: str) -> str:
        """Resolve MX record for domain, return highest-priority mail server."""
        import dns.resolver
        answers = dns.resolver.resolve(domain, 'MX')
        # Sort by priority (lowest number = highest priority)
        mx_records = sorted(answers, key=lambda r: r.preference)
        return str(mx_records[0].exchange).rstrip('.')

    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute SMTP email sending"""
        try:
            # Resolve email addresses from context if needed
            from_email = self._resolve_value(config['from_email'], context)
            to_email = self._resolve_value(config['to_email'], context)
            subject = self._resolve_value(config.get('subject', ''), context)
            from_name = self._resolve_value(config.get('from_name', ''), context)

            # Determine SMTP host and connection settings
            smtp_host = config.get('smtp_host', '').strip()
            if not smtp_host:
                # Direct delivery — resolve MX from recipient domain
                domain = to_email.split('@')[1]
                smtp_host = self._resolve_mx(domain)
                smtp_port = config.get('smtp_port') or 25
                use_tls = config.get('use_tls', False)
                logger.info(f"Direct delivery to MX: {smtp_host}:{smtp_port} for {domain}")
            else:
                smtp_port = config.get('smtp_port', 587)
                use_tls = config.get('use_tls', True)

            smtp_username = config.get('smtp_username')
            smtp_password = config.get('smtp_password')
            ehlo_hostname = config.get('ehlo_hostname', '').strip()

            # Build message structure
            attachments = config.get('attachments', [])
            body_text = self._resolve_value(config.get('body_text', ''), context)
            body_html = self._resolve_value(config.get('body_html', ''), context)

            if attachments:
                # Mixed message: body + attachments
                msg = MIMEMultipart('mixed')
                body_part = MIMEMultipart('alternative')
                if body_text:
                    body_part.attach(MIMEText(body_text, 'plain'))
                if body_html:
                    body_part.attach(MIMEText(body_html, 'html'))
                msg.attach(body_part)

                # Attach files
                for att in attachments:
                    path = self._resolve_value(att['path'], context)
                    filename = att.get('filename') or os.path.basename(path)
                    mime_type = att.get('mime_type', 'application/octet-stream')
                    maintype, subtype = mime_type.split('/', 1)
                    part = MIMEBase(maintype, subtype)
                    with open(path, 'rb') as f:
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', 'attachment', filename=filename)
                    msg.attach(part)
            else:
                # Simple alternative message (text/html)
                msg = MIMEMultipart('alternative')
                if body_text:
                    msg.attach(MIMEText(body_text, 'plain'))
                if body_html:
                    msg.attach(MIMEText(body_html, 'html'))

            # Set headers
            msg['From'] = f"{from_name} <{from_email}>" if from_name else from_email
            msg['To'] = to_email
            msg['Subject'] = subject

            # Apply custom headers
            custom_headers = config.get('custom_headers', {})
            for header_name, header_value in custom_headers.items():
                resolved = self._resolve_value(header_value, context)
                msg[header_name] = resolved

            # Connect and send
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            if ehlo_hostname:
                server.ehlo(ehlo_hostname)
            if use_tls:
                server.starttls()
                if ehlo_hostname:
                    server.ehlo(ehlo_hostname)

            if smtp_username and smtp_password:
                server.login(smtp_username, smtp_password)

            # Send using envelope_from if specified, otherwise use send_message
            envelope_from = config.get('envelope_from', '').strip()
            if envelope_from:
                envelope_from = self._resolve_value(envelope_from, context)
                server.sendmail(envelope_from, to_email, msg.as_string())
            else:
                server.send_message(msg)

            server.quit()

            context['_email_sent'] = {
                'to': to_email,
                'subject': subject,
                'timestamp': str(context.get('timestamp', ''))
            }

            logger.info(f"Email sent to {to_email}: {subject}")

        except Exception as e:
            logger.error(f"SMTP email sending failed: {e}")
            return self.on_error(e, context, config)

        return context

    def _resolve_value(self, value: str, context: Dict[str, Any]) -> str:
        """Resolve template variables in value (supports nested paths e.g. {{target.email}})."""
        if not isinstance(value, str):
            return str(value)
        return interpolate_string(value, context)

    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate SMTP configuration"""
        errors = []

        if not config.get('from_email'):
            errors.append("from_email is required")

        if not config.get('to_email'):
            errors.append("to_email is required")

        smtp_port = config.get('smtp_port', 587)
        if not isinstance(smtp_port, int) or smtp_port < 1 or smtp_port > 65535:
            errors.append("smtp_port must be between 1 and 65535")

        return errors if errors else None
