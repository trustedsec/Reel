"""
Caddy web server automation service
Manages Caddy configuration via API for campaign deployment
"""
import json
import logging
import os
import re
import requests
import ssl
import socket
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)


class CaddySSLError(Exception):
    """SSL certificate related errors"""
    pass


class CaddyAPIError(Exception):
    """Caddy API communication errors"""
    pass


class CaddyManager:
    """Manages Caddy web server configuration and SSL certificates"""
    
    def __init__(self, caddy_api_url: str = None, storage_base: str = None):
        from shared.config import Config
        
        config = Config()
        self._caddy_api_url_override = caddy_api_url  # Store override if provided
        self._storage_base_override = storage_base  # Store override if provided
        self._config = config  # Store config reference for dynamic reading
        self.storage_base = Path(storage_base or config.CADDY_STORAGE_BASE)
        self.campaigns_dir = self.storage_base / "campaigns"
        self.email = config.CADDY_EMAIL
        self.default_domain = config.CADDY_DEFAULT_DOMAIN
        self.enable_staging = config.CADDY_ENABLE_STAGING
        
        # Ensure storage directories exist
        self.campaigns_dir.mkdir(parents=True, exist_ok=True)
    
    @property
    def caddy_api_url(self):
        """Get Caddy API URL dynamically from config (reads from database if updated)"""
        if self._caddy_api_url_override:
            return self._caddy_api_url_override.rstrip('/')
        # Re-read from config to get latest database value
        return self._config.CADDY_API_URL.rstrip('/')
        
    def validate_ssl_certificate(self, cert_data: bytes, key_data: bytes, domain: Optional[str] = None) -> Dict:
        """
        Validate SSL certificate and key pair
        
        Args:
            cert_data: Certificate PEM data
            key_data: Private key PEM data  
            domain: Optional domain to validate against certificate
            
        Returns:
            Dict with validation results and certificate info
            
        Raises:
            CaddySSLError: If validation fails
        """
        try:
            # Load certificate
            cert = x509.load_pem_x509_certificate(cert_data)
            
            # Load private key
            try:
                private_key = serialization.load_pem_private_key(key_data, password=None)
            except Exception as e:
                raise CaddySSLError(f"Invalid private key: {e}")
            
            # Verify key matches certificate
            cert_public_key = cert.public_key()
            private_public_key = private_key.public_key()
            
            # Compare public key components (basic check)
            if cert_public_key.key_size != private_public_key.key_size:
                raise CaddySSLError("Certificate and private key do not match")
            
            # Extract certificate information
            subject = cert.subject
            issuer = cert.issuer
            
            # Get common name
            common_name = None
            for attribute in subject:
                if attribute.oid == NameOID.COMMON_NAME:
                    common_name = attribute.value
                    break
            
            # Get subject alternative names
            san_list = []
            try:
                san_extension = cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                san_list = [name.value for name in san_extension.value]
            except x509.ExtensionNotFound:
                pass
            
            # Check domain if provided
            domain_valid = True
            if domain:
                domain_valid = (domain == common_name or domain in san_list)
            
            # Check certificate validity
            now = datetime.utcnow()
            is_expired = now > cert.not_valid_after
            expires_soon = (cert.not_valid_after - now).days < 30
            
            return {
                'valid': True,
                'common_name': common_name,
                'subject_alt_names': san_list,
                'issuer': issuer.rfc4514_string(),
                'not_before': cert.not_valid_before,
                'not_after': cert.not_valid_after,
                'is_expired': is_expired,
                'expires_soon': expires_soon,
                'domain_valid': domain_valid,
                'days_until_expiry': (cert.not_valid_after - now).days
            }
            
        except Exception as e:
            if isinstance(e, CaddySSLError):
                raise
            raise CaddySSLError(f"Certificate validation failed: {e}")
    
    def store_ssl_files(self, campaign_uid: str, cert_data: bytes, key_data: bytes, ca_data: Optional[bytes] = None) -> Dict[str, str]:
        """
        Store SSL certificate files securely in campaign directory
        
        Args:
            campaign_uid: Campaign unique identifier
            cert_data: Certificate PEM data
            key_data: Private key PEM data
            ca_data: Optional CA chain PEM data
            
        Returns:
            Dict with file paths
        """
        ssl_dir = self.campaigns_dir / campaign_uid / "ssl"
        ssl_dir.mkdir(parents=True, exist_ok=True)
        
        cert_path = ssl_dir / "cert.pem"
        key_path = ssl_dir / "key.pem"
        ca_path = ssl_dir / "ca.pem" if ca_data else None
        config_path = ssl_dir / "config.json"
        
        # Write certificate
        cert_path.write_bytes(cert_data)
        cert_path.chmod(0o644)
        
        # Write private key with restricted permissions
        key_path.write_bytes(key_data)
        key_path.chmod(0o600)
        
        # Write CA chain if provided
        if ca_data and ca_path:
            ca_path.write_bytes(ca_data)
            ca_path.chmod(0o644)
        
        # Store metadata
        config = {
            'created_at': datetime.utcnow().isoformat(),
            'cert_path': str(cert_path),
            'key_path': str(key_path),
            'ca_path': str(ca_path) if ca_path else None
        }
        
        config_path.write_text(json.dumps(config, indent=2))
        
        logger.info(f"SSL files stored for campaign {campaign_uid}")
        
        return {
            'cert_path': str(cert_path),
            'key_path': str(key_path),
            'ca_path': str(ca_path) if ca_path else None,
            'config_path': str(config_path)
        }
    
    def generate_self_signed_certificate(self, campaign_uid: str, domain: str) -> Dict[str, str]:
        """
        Generate self-signed certificate for testing
        
        Args:
            campaign_uid: Campaign unique identifier
            domain: Domain name for certificate
            
        Returns:
            Dict with certificate file paths
        """
        # Generate private key
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        
        # Create certificate
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Testing"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Local"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Reel v2"),
            x509.NameAttribute(NameOID.COMMON_NAME, domain),
        ])
        
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            private_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.utcnow()
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=365)
        ).add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(domain),
            ]),
            critical=False,
        ).sign(private_key, hashes.SHA256())
        
        # Convert to PEM format
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        # Store files
        return self.store_ssl_files(campaign_uid, cert_pem, key_pem)
    
    def _build_ua_filter_regex(self, deny_list: List[str]) -> Optional[str]:
        """
        Build a Go RE2 regex pattern for User-Agent filtering.

        Args:
            deny_list: List of substring patterns to block (e.g. ['curl', 'bot'])

        Returns:
            RE2 regex string like ``(?i).*(curl|bot).*``, or None if the list is empty.
        """
        if not deny_list:
            return None

        escaped = [re.escape(entry) for entry in deny_list if entry]
        if not escaped:
            return None

        return f"(?i).*({'|'.join(escaped)}).*"

    def _prerender_blocked_template(self, campaign, template_id: int = None) -> Optional[str]:
        """
        Pre-render a template with campaign context.

        Per-request fields (ip_address, user_agent, path) resolve to empty
        strings since they aren't available at deploy time.

        Args:
            campaign: Campaign model instance
            template_id: Explicit template ID to render. Falls back to
                         campaign.ua_blocked_template_id when not supplied.

        Returns:
            Rendered HTML string, or None on failure.
        """
        from shared.database import Template
        from shared.template_render import render_sandboxed

        target_template_id = template_id or getattr(campaign, 'ua_blocked_template_id', None)
        if not target_template_id:
            return None

        try:
            blocked_template = Template.query.get(target_template_id)
            if not blocked_template or not blocked_template.template_html:
                logger.warning(f"Blocked template {target_template_id} not found or empty for campaign {campaign.uid}")
                return None

            template_context = {
                'campaign': {
                    'name': campaign.name,
                    'uid': campaign.uid,
                },
                'request': {
                    'ip_address': '',
                    'user_agent': '',
                    'path': '',
                },
                'variables': campaign.variables or {},
            }

            return render_sandboxed(blocked_template.template_html, template_context)
        except Exception as e:
            logger.error(f"Failed to pre-render blocked template for campaign {campaign.uid}: {e}")
            return None

    def _build_gate_filter_subroute(self, campaign) -> Optional[Dict]:
        """
        Build a Caddy subroute that blocks requests without the gate token.

        Returns None (skip subroute) when:
        - gate_enabled is False
        - gate_token is empty
        - gate_mode='template' but template pre-render fails
        - gate_mode='redirect' but redirect URL is empty

        Args:
            campaign: Campaign model instance

        Returns:
            Caddy subroute dict, or None to skip.
        """
        if not getattr(campaign, 'gate_enabled', False):
            return None

        gate_token = getattr(campaign, 'gate_token', None)
        if not gate_token:
            return None

        gate_param_name = getattr(campaign, 'gate_param_name', 'rid') or 'rid'
        gate_mode = getattr(campaign, 'gate_mode', 'template') or 'template'

        # Build the response handler based on gate_mode
        if gate_mode == 'redirect':
            redirect_url = getattr(campaign, 'gate_redirect_url', None)
            if not redirect_url:
                logger.warning(f"Gate mode is 'redirect' but no URL set for campaign {campaign.uid}, skipping gate")
                return None
            handler = {
                "handler": "static_response",
                "status_code": 302,
                "headers": {
                    "Location": [redirect_url]
                }
            }
        else:
            # template mode
            gate_template_id = getattr(campaign, 'gate_template_id', None)
            if not gate_template_id:
                logger.warning(f"Gate mode is 'template' but no template set for campaign {campaign.uid}, skipping gate")
                return None
            rendered_html = self._prerender_blocked_template(campaign, template_id=gate_template_id)
            if rendered_html is None:
                logger.warning(f"Failed to pre-render gate template for campaign {campaign.uid}, skipping gate")
                return None
            handler = {
                "handler": "static_response",
                "status_code": 200,
                "headers": {
                    "Content-Type": ["text/html; charset=utf-8"]
                },
                "body": rendered_html
            }

        # Block ONLY GET/HEAD requests that:
        #   - don't carry the gate token in the query, AND
        #   - aren't on an exempt path (assets, tracking, health checks, etc.)
        # Anything else (POSTs, asset loads, callbacks) passes through to Flask.
        # Method is matched explicitly (rather than via 'not method=POST') because
        # Caddy's `not` over a list of matcher sets has ambiguous semantics for
        # multi-condition AND — explicit positive method match is unambiguous.
        exempt_paths = [
            "/track/*",
            "/proxy-status/*",
            "/callback",
            "/health",
            "/js/*",
            "/static/*",
            "/assets/*",
            "/media/*",
            "/m/*",
            "/favicon.ico",
            "/robots.txt",
        ]
        return {
            "match": [{
                "method": ["GET", "HEAD"],
                "not": [
                    {"query": {gate_param_name: [gate_token]}},
                    {"path": exempt_paths},
                ]
            }],
            "handle": [handler],
            "terminal": True
        }

    def generate_caddy_config(self, campaign) -> Dict:
        """
        Generate Caddy route entry and TLS config for a campaign.

        Returns a dict with:
            - route: host-matched route entry for the shared server block
            - tls: TLS config dict (for custom certs) or None
            - ssl_mode: the campaign's ssl_mode string

        Args:
            campaign: Campaign model instance

        Returns:
            Dict with route, tls, and ssl_mode keys
        """
        from shared.database import Campaign  # Import here to avoid circular imports
        from shared.config import Config

        # Outbound campaigns: only generate route if MMS is enabled and a domain is set.
        # The route is minimal — just a reverse proxy so /m/<uuid> is reachable.
        if campaign.campaign_type == 'outbound':
            if not getattr(campaign, 'is_mms_enabled', False) or not campaign.custom_domain:
                raise ValueError(
                    "Outbound campaign needs is_mms_enabled=True and custom_domain set "
                    "to generate a Caddy route."
                )
            return self._generate_outbound_mms_config(campaign)

        if campaign.campaign_type != 'inbound':
            raise ValueError(f"Cannot generate Caddy config for {campaign.campaign_type} campaign.")

        # Determine domain
        if campaign.custom_domain:
            domain = campaign.custom_domain
        else:
            domain = f"campaign-{campaign.uid}.{self.default_domain}"

        # Get template assets directory if campaign has a template
        template_assets_dir = None
        if campaign.template_id:
            template_assets_dir = self._config.TEMPLATES_FOLDER / str(campaign.template_id) / "assets"
            template_assets_dir.mkdir(parents=True, exist_ok=True)

        # Build subroute handlers for this campaign
        subroutes = []

        # UA bot filter — block matching User-Agents at Caddy level
        if getattr(campaign, 'ua_filter_enabled', False):
            ua_regex = self._build_ua_filter_regex(getattr(campaign, 'ua_deny_list', None) or [])
            ua_html = self._prerender_blocked_template(campaign) if ua_regex else None
            if ua_regex and ua_html is not None:
                subroutes.append({
                    "match": [{
                        "header_regexp": {
                            "User-Agent": {
                                "name": "ua_block",
                                "pattern": ua_regex
                            }
                        }
                    }],
                    "handle": [{
                        "handler": "static_response",
                        "status_code": 200,
                        "headers": {
                            "Content-Type": ["text/html; charset=utf-8"]
                        },
                        "body": ua_html
                    }],
                    "terminal": True
                })

        # Asset handlers — placed BEFORE the gate so static/template assets
        # always serve cleanly even when the gate is enabled. Each asset route
        # is terminal, so a matching request is consumed and never reaches the
        # gate matcher (which is sensitive to Caddy's `not` semantics).

        # Serve template assets if available
        if template_assets_dir and template_assets_dir.exists():
            subroutes.append({
                "match": [{"path": ["/assets/*"]}],
                "handle": [
                    {
                        "handler": "rewrite",
                        "strip_path_prefix": "/assets"
                    },
                    {
                        "handler": "file_server",
                        "root": str(template_assets_dir),
                        "index_names": []
                    }
                ],
                "terminal": True
            })

        # Serve global static assets
        subroutes.append({
            "match": [{"path": ["/static/*"]}],
            "handle": [
                {
                    "handler": "file_server",
                    "root": str(self._config.ASSETS_FOLDER),
                    "index_names": []
                }
            ],
            "terminal": True
        })

        # Gate token filter — block GET requests without the gate token.
        # Runs AFTER asset handlers so /static and /assets always pass.
        gate_subroute = self._build_gate_filter_subroute(campaign)
        if gate_subroute:
            subroutes.append(gate_subroute)

        # Proxy everything else to the phishing server
        # X-Forwarded-Proto: explicit so Flask sees correct scheme when Caddy is behind Cloudflare
        # (Caddy's default uses connection-to-Caddy, which is HTTP when Cloudflare uses Flexible SSL)
        proto = "https" if campaign.ssl_mode != "disabled" else "http"
        # When behind Cloudflare, ssl_mode may be disabled yet clients use HTTPS; prefer https
        if campaign.ssl_mode == "disabled" and campaign.custom_domain:
            proto = "https"
        subroutes.append({
            "handle": [
                {
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": f"localhost:{self._config.PHISHING_PORT}"}],
                    "headers": {
                        "request": {
                            "set": {
                                "X-Campaign-ID": [campaign.uid],
                                "X-Campaign-Domain": [domain],
                                "X-Template-ID": [str(campaign.template_id)] if campaign.template_id else ["none"],
                                "X-Get-Workflow-ID": [str(campaign.get_workflow_id)] if campaign.get_workflow_id else ["none"],
                                "X-Post-Workflow-ID": [str(campaign.post_workflow_id)] if campaign.post_workflow_id else ["none"],
                                "X-Forwarded-Proto": [proto],
                            }
                        }
                    }
                }
            ]
        })

        # Build the host-matched route entry
        route = {
            "match": [{"host": [domain]}],
            "handle": [
                {
                    "handler": "subroute",
                    "routes": subroutes
                }
            ],
            "terminal": True
        }

        # Build TLS config for custom certs
        tls_config = None
        if campaign.ssl_mode == 'custom' and campaign.ssl_cert_path:
            tls_config = {
                "certificates": {
                    "load_files": [
                        {
                            "certificate": campaign.ssl_cert_path,
                            "key": campaign.ssl_key_path
                        }
                    ]
                }
            }

        return {
            "route": route,
            "tls": tls_config,
            "ssl_mode": campaign.ssl_mode or "automatic"
        }
    
    def _generate_outbound_mms_config(self, campaign) -> Dict:
        """
        Minimal Caddy route for outbound campaigns with MMS enabled.
        Just proxies the configured custom_domain to the phishing server so
        /m/<uuid> is publicly reachable for Twilio to fetch card images.
        No template assets, gate, UA filter, or campaign headers needed.
        """
        domain = campaign.custom_domain
        proto = "https" if campaign.ssl_mode and campaign.ssl_mode != "disabled" else "http"
        if campaign.ssl_mode == "disabled":
            proto = "https"  # behind Cloudflare-style flexible SSL

        route = {
            "match": [{"host": [domain]}],
            "handle": [
                {
                    "handler": "subroute",
                    "routes": [
                        {
                            "handle": [
                                {
                                    "handler": "reverse_proxy",
                                    "upstreams": [{"dial": f"localhost:{self._config.PHISHING_PORT}"}],
                                    "headers": {
                                        "request": {
                                            "set": {
                                                "X-Campaign-Domain": [domain],
                                                "X-Forwarded-Proto": [proto],
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    ]
                }
            ],
            "terminal": True
        }

        tls_config = None
        if campaign.ssl_mode == 'custom' and campaign.ssl_cert_path:
            tls_config = {
                "certificates": {
                    "load_files": [
                        {
                            "certificate": campaign.ssl_cert_path,
                            "key": campaign.ssl_key_path
                        }
                    ]
                }
            }

        return {
            "route": route,
            "tls": tls_config,
            "ssl_mode": campaign.ssl_mode or "automatic"
        }

    def _get_default_catch_all_route(self) -> Dict:
        """
        Return a catch-all route for non-matching traffic (e.g. wrong host).
        This route has no host match so it acts as the fallback when appended
        as the last route in the shared server block.
        """
        return {
            "handle": [
                {
                    "handler": "static_response",
                    "status_code": 404,
                    "body": "Not Found"
                }
            ]
        }
    
    def _build_servers_from_db(self) -> Tuple[Dict, List[Dict]]:
        """
        Build HTTP servers dict from active inbound campaigns in DB.
        All campaign routes are merged into a single ``srv_campaigns`` server
        block so that Caddy never receives duplicate listen addresses.

        Returns:
            Tuple of (servers dict, list of TLS configs from campaigns with custom certs)
        """
        from shared.database import Campaign

        routes = []
        tls_configs = []
        ssl_modes = []

        from sqlalchemy import and_, or_
        active_campaigns = Campaign.query.filter(
            Campaign.status == 'active',
            or_(
                Campaign.campaign_type == 'inbound',
                and_(
                    Campaign.campaign_type == 'outbound',
                    Campaign.is_mms_enabled == True,
                    Campaign.custom_domain.isnot(None),
                ),
            )
        ).all()

        for c in active_campaigns:
            try:
                result = self.generate_caddy_config(c)
                routes.append(result['route'])
                ssl_modes.append(result['ssl_mode'])
                if result['tls'] is not None:
                    tls_configs.append(result['tls'])
            except Exception as e:
                logger.warning(f"Failed to generate Caddy config for campaign {c.uid}: {e}")
                continue

        # Append the default catch-all 404 route as the last route
        routes.append(self._get_default_catch_all_route())

        # Determine listen ports and automatic_https behaviour
        all_disabled = all(m == 'disabled' for m in ssl_modes) if ssl_modes else True
        needs_443 = any(m != 'disabled' for m in ssl_modes)

        listen = [":80"]
        if needs_443:
            listen.append(":443")

        srv_campaigns = {
            "listen": listen,
            "routes": routes,
        }

        if all_disabled:
            srv_campaigns["automatic_https"] = {"disable": True}

        servers = {"srv_campaigns": srv_campaigns}
        return servers, tls_configs

    def deploy_campaign_config(self, campaign) -> bool:
        """
        Deploy campaign configuration to Caddy.
        Syncs full config from DB (active inbound campaigns only), ensuring
        no orphan entries persist. Rebuilds config on each deploy.

        Args:
            campaign: Campaign model instance (used for log message; all active
                      inbound campaigns are deployed)

        Returns:
            True if deployment successful

        Raises:
            CaddyAPIError: If deployment fails
        """
        try:
            # Build servers from DB (source of truth) - removes orphan Caddy entries
            servers, tls_configs = self._build_servers_from_db()

            # Get current config for admin, logging, etc.
            current_result = self.get_current_config()
            current_config = current_result.get('config', {}) if current_result.get('success') else {}
            if not current_config:
                current_config = {}

            apps = current_config.get('apps', {})
            http_app = apps.get('http', {})

            # Build merged config: preserve other top-level keys (admin, logging, etc.)
            merged = {}
            for key in current_config:
                if key != 'apps':
                    merged[key] = current_config[key]
            if 'admin' not in merged:
                merged['admin'] = {'listen': ':2019'}
            merged['apps'] = dict(apps)
            merged['apps']['http'] = dict(http_app)
            merged['apps']['http']['servers'] = servers

            # Merge TLS from all campaigns with custom certs
            for tls_cfg in tls_configs:
                if 'tls' not in merged['apps']:
                    merged['apps']['tls'] = dict(tls_cfg)
                else:
                    existing = merged['apps']['tls'].get('certificates', {}).get('load_files', [])
                    new_files = tls_cfg.get('certificates', {}).get('load_files', [])
                    merged['apps']['tls'] = dict(merged['apps']['tls'])
                    merged['apps']['tls'].setdefault('certificates', {})
                    merged['apps']['tls']['certificates'] = dict(merged['apps']['tls']['certificates'])
                    merged['apps']['tls']['certificates']['load_files'] = existing + new_files

            # Post merged configuration to Caddy API
            response = requests.post(
                f"{self.caddy_api_url}/config/",
                json=merged,
                timeout=30
            )

            if response.status_code == 200:
                logger.info(f"Successfully deployed Caddy config for campaign {campaign.uid}")
                return True
            else:
                error_msg = f"Caddy API error: {response.status_code} - {response.text}"
                logger.error(error_msg)
                raise CaddyAPIError(error_msg)

        except requests.RequestException as e:
            error_msg = f"Failed to connect to Caddy API: {e}"
            logger.error(error_msg)
            raise CaddyAPIError(error_msg)
    
    def remove_campaign_config(self, campaign_uid: str) -> bool:
        """
        Remove campaign configuration from Caddy by rebuilding from DB.

        The caller should mark the campaign as non-active in the DB before
        calling this method.  The rebuild will exclude the deactivated
        campaign automatically.

        Args:
            campaign_uid: Campaign unique identifier

        Returns:
            True if removal successful
        """
        try:
            # Rebuild the full config from DB; the deactivated campaign
            # is already excluded because its status is no longer 'active'.
            servers, tls_configs = self._build_servers_from_db()

            current_result = self.get_current_config()
            current_config = current_result.get('config', {}) if current_result.get('success') else {}
            if not current_config:
                current_config = {}

            apps = current_config.get('apps', {})
            http_app = apps.get('http', {})

            merged = {}
            for key in current_config:
                if key != 'apps':
                    merged[key] = current_config[key]
            if 'admin' not in merged:
                merged['admin'] = {'listen': ':2019'}
            merged['apps'] = dict(apps)
            merged['apps']['http'] = dict(http_app)
            merged['apps']['http']['servers'] = servers

            # Merge TLS from remaining campaigns with custom certs
            if tls_configs:
                merged_tls: Dict = {}
                for tls_cfg in tls_configs:
                    if not merged_tls:
                        merged_tls = dict(tls_cfg)
                    else:
                        existing = merged_tls.get('certificates', {}).get('load_files', [])
                        new_files = tls_cfg.get('certificates', {}).get('load_files', [])
                        merged_tls.setdefault('certificates', {})
                        merged_tls['certificates'] = dict(merged_tls['certificates'])
                        merged_tls['certificates']['load_files'] = existing + new_files
                merged['apps']['tls'] = merged_tls
            else:
                merged['apps'].pop('tls', None)

            response = requests.post(
                f"{self.caddy_api_url}/config/",
                json=merged,
                timeout=30
            )

            if response.status_code == 200:
                logger.info(f"Removed Caddy config for campaign {campaign_uid} (rebuilt from DB)")
                return True
            else:
                logger.error(f"Failed to remove Caddy config: {response.status_code} - {response.text}")
                return False

        except requests.RequestException as e:
            logger.error(f"Failed to connect to Caddy API for removal: {e}")
            return False
    
    def test_ssl_endpoint(self, domain: str, port: int = 443) -> Dict:
        """
        Test SSL endpoint accessibility and certificate validity
        
        Args:
            domain: Domain to test
            port: Port to test (default 443)
            
        Returns:
            Dict with test results
        """
        try:
            # Create SSL context
            context = ssl.create_default_context()
            
            # Connect and get certificate
            with socket.create_connection((domain, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert_der = ssock.getpeercert(binary_form=True)
                    cert = x509.load_der_x509_certificate(cert_der)
                    
                    return {
                        'accessible': True,
                        'certificate_valid': True,
                        'expires': cert.not_valid_after,
                        'issuer': cert.issuer.rfc4514_string(),
                        'error': None
                    }
                    
        except Exception as e:
            return {
                'accessible': False,
                'certificate_valid': False,
                'expires': None,
                'issuer': None,
                'error': str(e)
            }
    
    def get_caddy_status(self) -> Dict:
        """
        Get Caddy server status
        
        Returns:
            Dict with Caddy status information
        """
        try:
            response = requests.get(f"{self.caddy_api_url}/config/", timeout=10)
            if response.status_code == 200:
                return {
                    'running': True,
                    'config': response.json(),
                    'error': None
                }
            else:
                return {
                    'running': False,
                    'config': None,
                    'error': f"HTTP {response.status_code}"
                }
        except requests.RequestException as e:
            return {
                'running': False,
                'config': None,
                'error': str(e)
            }
    
    def get_template_assets_dir(self, template_id: int) -> Path:
        """
        Get the assets directory for a template
        
        Args:
            template_id: Template ID
            
        Returns:
            Path to template assets directory
        """
        assets_dir = self._config.TEMPLATES_FOLDER / str(template_id) / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        return assets_dir
    
    def upload_template_asset(self, template_id: int, filename: str, file_data: bytes) -> str:
        """
        Upload an asset file for a template
        
        Args:
            template_id: Template ID
            filename: Name of the file
            file_data: File content as bytes
            
        Returns:
            Relative URL path to the uploaded asset
        """
        assets_dir = self.get_template_assets_dir(template_id)
        
        # Sanitize filename
        import os
        import re
        filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
        
        file_path = assets_dir / filename
        file_path.write_bytes(file_data)
        
        logger.info(f"Uploaded asset {filename} for template {template_id}")
        
        return f"/assets/{filename}"
    
    def list_template_assets(self, template_id: int) -> List[Dict]:
        """
        List all assets for a template
        
        Args:
            template_id: Template ID
            
        Returns:
            List of asset information dictionaries
        """
        assets_dir = self.get_template_assets_dir(template_id)
        assets = []
        
        if assets_dir.exists():
            for file_path in assets_dir.iterdir():
                if file_path.is_file():
                    stat = file_path.stat()
                    assets.append({
                        'filename': file_path.name,
                        'size': stat.st_size,
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        'url': f"/assets/{file_path.name}"
                    })
        
        return sorted(assets, key=lambda x: x['filename'])
    
    def delete_template_asset(self, template_id: int, filename: str) -> bool:
        """
        Delete an asset file for a template
        
        Args:
            template_id: Template ID
            filename: Name of the file to delete
            
        Returns:
            True if file was deleted, False if not found
        """
        assets_dir = self.get_template_assets_dir(template_id)
        file_path = assets_dir / filename
        
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
            logger.info(f"Deleted asset {filename} for template {template_id}")
            return True
        
        return False

    def cleanup_campaign_files(self, campaign_uid: str):
        """
        Clean up all files associated with a campaign
        
        Args:
            campaign_uid: Campaign unique identifier
        """
        campaign_dir = self.campaigns_dir / campaign_uid
        if campaign_dir.exists():
            import shutil
            shutil.rmtree(campaign_dir)
            logger.info(f"Cleaned up files for campaign {campaign_uid}")
    
    def get_current_config(self) -> Dict:
        """
        Get current Caddy configuration from API
        
        Returns:
            Dict with current config or error information
        """
        # Check if port is accessible before making request
        try:
            from urllib.parse import urlparse
            parsed = urlparse(self.caddy_api_url)
            host = parsed.hostname or 'localhost'
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            
            # Quick socket test to see if port is open
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_socket.settimeout(2)
            try:
                result = test_socket.connect_ex((host, port))
                test_socket.close()
                if result != 0:
                    return {
                        'success': False,
                        'config': None,
                        'error': f"Caddy API server is not accessible at {host}:{port}. Please ensure Caddy is running and the admin API is enabled. Start Caddy with: ./start.sh --with-caddy"
                    }
            except Exception:
                # Continue with request attempt even if socket test fails
                pass
        except Exception:
            # Continue with request attempt
            pass
        
        try:
            response = requests.get(f"{self.caddy_api_url}/config/", timeout=10)
            if response.status_code == 200:
                return {
                    'success': True,
                    'config': response.json(),
                    'error': None
                }
            else:
                return {
                    'success': False,
                    'config': None,
                    'error': f"HTTP {response.status_code}: {response.text}"
                }
        except requests.exceptions.ConnectionError as e:
            return {
                'success': False,
                'config': None,
                'error': f"Caddy API server is not accessible at {self.caddy_api_url}. Please ensure Caddy is running and the admin API is enabled. Start Caddy with: ./start.sh --with-caddy"
            }
        except requests.RequestException as e:
            return {
                'success': False,
                'config': None,
                'error': f"Failed to connect to Caddy API: {str(e)}. Please ensure Caddy is running and accessible at {self.caddy_api_url}"
            }
    
    def update_config(self, config_dict: Dict) -> Dict:
        """
        Update Caddy configuration via API
        
        Args:
            config_dict: New Caddy configuration dictionary
            
        Returns:
            Dict with success status and error information
        """
        try:
            response = requests.post(
                f"{self.caddy_api_url}/config/",
                json=config_dict,
                timeout=30
            )
            if response.status_code == 200:
                logger.info("Successfully updated Caddy configuration")
                return {
                    'success': True,
                    'error': None
                }
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                logger.error(f"Failed to update Caddy config: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
        except requests.RequestException as e:
            error_msg = f"Failed to connect to Caddy API: {e}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
    
    def backup_config(self) -> bool:
        """
        Backup current Caddy configuration to file
        
        Returns:
            True if backup successful
        """
        try:
            result = self.get_current_config()
            if not result['success']:
                logger.error(f"Failed to get config for backup: {result['error']}")
                return False
            
            backup_dir = self.storage_base / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            backup_file = backup_dir / f"caddy_config_backup_{timestamp}.json"
            
            with open(backup_file, 'w') as f:
                json.dump(result['config'], f, indent=2)
            
            # Also save as latest backup
            latest_backup = backup_dir / "caddy_config_backup_latest.json"
            with open(latest_backup, 'w') as f:
                json.dump(result['config'], f, indent=2)
            
            logger.info(f"Caddy config backed up to {backup_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to backup Caddy config: {e}")
            return False
    
    def restore_config(self) -> bool:
        """
        Restore Caddy configuration from latest backup
        
        Returns:
            True if restore successful
        """
        try:
            backup_dir = self.storage_base / "backups"
            latest_backup = backup_dir / "caddy_config_backup_latest.json"
            
            if not latest_backup.exists():
                logger.error("No backup found to restore")
                return False
            
            with open(latest_backup, 'r') as f:
                config = json.load(f)
            
            result = self.update_config(config)
            if result['success']:
                logger.info("Caddy config restored from backup")
                return True
            else:
                logger.error(f"Failed to restore config: {result['error']}")
                return False
        except Exception as e:
            logger.error(f"Failed to restore Caddy config: {e}")
            return False
    
    def enable_panic_mode(self) -> Dict:
        """
        Enable panic mode - redirect all traffic to microsoft.com
        
        Returns:
            Dict with success status and error information
        """
        try:
            # Backup current config before enabling panic
            if not self.backup_config():
                logger.warning("Failed to backup config before panic mode, continuing anyway")
            
            # Get current config to preserve admin API and other global settings
            current_config_result = self.get_current_config()
            current_config = current_config_result.get('config', {}) if current_config_result.get('success') else {}
            
            # Generate panic config that redirects all hosts to microsoft.com
            # Preserve admin API configuration and other global settings
            panic_config = {}
            
            # Preserve admin API configuration if it exists
            if 'admin' in current_config:
                panic_config['admin'] = current_config['admin']
            else:
                # Default admin API config if not present
                panic_config['admin'] = {
                    "listen": ":2019"
                }
            
            # Preserve any other top-level config (like logging, etc.)
            for key in current_config:
                if key not in ['apps', 'admin']:
                    panic_config[key] = current_config[key]
            
            # Replace HTTP app with panic mode routes
            panic_config["apps"] = {
                "http": {
                    "servers": {
                        "panic_mode": {
                            "routes": [
                                {
                                    "match": [{"host": ["*"]}],
                                    "handle": [{
                                        "handler": "static_response",
                                        "status_code": 302,
                                        "headers": {
                                            "Location": ["https://www.microsoft.com"]
                                        }
                                    }]
                                }
                            ]
                        }
                    }
                }
            }
            
            result = self.update_config(panic_config)
            if result['success']:
                # Store panic state
                panic_state_file = self.storage_base / "panic_state.json"
                with open(panic_state_file, 'w') as f:
                    json.dump({
                        'enabled': True,
                        'enabled_at': datetime.utcnow().isoformat()
                    }, f, indent=2)
                logger.warning("PANIC MODE ENABLED - All traffic redirected to microsoft.com")
            
            return result
        except Exception as e:
            logger.error(f"Failed to enable panic mode: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def disable_panic_mode(self) -> Dict:
        """
        Disable panic mode - restore original configuration
        
        Returns:
            Dict with success status and error information
        """
        try:
            result = self.restore_config()
            if result:
                # Clear panic state
                panic_state_file = self.storage_base / "panic_state.json"
                if panic_state_file.exists():
                    panic_state_file.unlink()
                logger.info("Panic mode disabled, original config restored")
                return {
                    'success': True,
                    'error': None
                }
            else:
                return {
                    'success': False,
                    'error': 'Failed to restore config from backup'
                }
        except Exception as e:
            logger.error(f"Failed to disable panic mode: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_panic_status(self) -> Dict:
        """
        Get current panic mode status
        
        Returns:
            Dict with panic status information
        """
        try:
            panic_state_file = self.storage_base / "panic_state.json"
            if panic_state_file.exists():
                with open(panic_state_file, 'r') as f:
                    state = json.load(f)
                return {
                    'enabled': state.get('enabled', False),
                    'enabled_at': state.get('enabled_at'),
                    'error': None
                }
            else:
                return {
                    'enabled': False,
                    'enabled_at': None,
                    'error': None
                }
        except Exception as e:
            logger.error(f"Failed to get panic status: {e}")
            return {
                'enabled': False,
                'enabled_at': None,
                'error': str(e)
            }


# Global instance
caddy_manager = CaddyManager()