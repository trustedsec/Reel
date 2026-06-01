"""
Cloudflare Turnstile verification - shared helper for campaign-level and plugin use.
"""
from typing import Optional
import requests
import logging

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile(
    token: str,
    secret_key: str,
    remote_ip: Optional[str] = None,
    timeout: int = 5,
) -> bool:
    """
    Verify a Cloudflare Turnstile token with the siteverify API.

    Args:
        token: The cf-turnstile-response token from the client.
        secret_key: Turnstile secret key for the site.
        remote_ip: Optional client IP for remoteip parameter.
        timeout: Request timeout in seconds.

    Returns:
        True if verification succeeded, False otherwise.
    """
    if not token or not secret_key:
        logger.warning("Turnstile verify: missing token or secret_key")
        return False
    try:
        data = {"secret": secret_key, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        response = requests.post(VERIFY_URL, data=data, timeout=timeout)
        if response.status_code != 200:
            logger.error("Turnstile API returned status %s", response.status_code)
            return False
        result = response.json()
        success = result.get("success", False)
        if not success:
            error_codes = result.get("error-codes", [])
            logger.warning("Turnstile validation failed: %s", error_codes)
        return success
    except requests.RequestException as e:
        logger.error("Turnstile API request failed: %s", e)
        return False
    except Exception as e:
        logger.error("Turnstile verification error: %s", e)
        return False
