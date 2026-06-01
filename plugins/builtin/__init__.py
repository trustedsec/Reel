"""
Built-in plugins for Reel v2
"""
from .slack import SlackPlugin
from .pushover import PushoverPlugin
from .aws_connect_dialer import AWSConnectDialerPlugin
from .graphspy import GraphSpyPlugin
from .data_transform import DataTransformPlugin
from .conditional import ConditionalPlugin
from .delay import DelayPlugin
from .smtp_sender import SMTPEmailSenderPlugin
from .target_selector import TargetSelectorPlugin
from .email_validator import EmailValidatorPlugin
from .phishing_detector import PhishingDetectorPlugin
from .render_template import RenderTemplatePlugin
from .useragent_check import UserAgentCheckPlugin
from .redirect import RedirectPlugin
from .log_event import LogEventPlugin
from .capture_credentials import CaptureCredentialsPlugin
from .queue_credential_proxy import QueueCredentialProxyPlugin
from .validate_input import ValidateInputPlugin
from .captcha import CaptchaPlugin
from .url_obfuscator import UrlObfuscatorPlugin
from .sms_sender import TwilioSmsSenderPlugin
from .sync_credential_proxy import SyncCredentialProxyPlugin
from .text_to_image import TextToImagePlugin
from .qr_code import QrCodePlugin

__all__ = [
    'SlackPlugin',
    'PushoverPlugin',
    'AWSConnectDialerPlugin',
    'GraphSpyPlugin',
    'DataTransformPlugin',
    'ConditionalPlugin',
    'DelayPlugin',
    'SMTPEmailSenderPlugin',
    'TargetSelectorPlugin',
    'EmailValidatorPlugin',
    'PhishingDetectorPlugin',
    'RenderTemplatePlugin',
    'UserAgentCheckPlugin',
    'RedirectPlugin',
    'LogEventPlugin',
    'CaptureCredentialsPlugin',
    'QueueCredentialProxyPlugin',
    'ValidateInputPlugin',
    'CaptchaPlugin',
    'UrlObfuscatorPlugin',
    'TwilioSmsSenderPlugin',
    'SyncCredentialProxyPlugin',
    'TextToImagePlugin',
    'QrCodePlugin'
]

