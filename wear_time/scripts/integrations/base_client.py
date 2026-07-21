"""
Base API client with shared retry logic, rate limiting, and pagination.

Extracted from dashboard/data_layer.py's _request_with_retry() pattern
and mixpanel_cohort_insights/mixpanel_client.py's class structure.
All source-specific clients subclass this.
"""

import logging
import os
import time
from typing import Any, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# Global: internal domains to exclude from all customer queries.
# Matches global Rule 8 in master.agent.md.
INTERNAL_DOMAINS = {"nextsense.com", "test.com", "example.com"}


def is_internal_email(email: str) -> bool:
    """Check if an email belongs to an internal/test domain."""
    if not email or "@" not in email:
        return False
    domain = email.split("@")[-1].lower()
    return domain in INTERNAL_DOMAINS


def load_env(env_path: Optional[str] = None):
    """Load .env file into os.environ. Lightweight — no external dependency."""
    if env_path is None:
        # Default: .env in the same directory as this file
        env_path = os.path.join(os.path.dirname(__file__), ".env")

    if not os.path.exists(env_path):
        log.warning(f".env not found at {env_path}")
        return

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and value:
                os.environ.setdefault(key, value)


class BaseClient:
    """Base API client with retry, rate limiting, and pagination.

    Subclasses must set:
        - self.base_url
        - self.headers
    in their __init__().
    """

    def __init__(self, base_url: str, headers: dict, rate_limit_delay: float = 0.0):
        """
        Args:
            base_url: API base URL (e.g., 'https://api.rechargeapps.com')
            headers: Default headers for every request (auth, content-type, etc.)
            rate_limit_delay: Minimum seconds between requests (0 = no delay)
        """
        self.base_url = base_url.rstrip("/")
        self.headers = headers
        self.rate_limit_delay = rate_limit_delay
        self._last_request_time = 0.0

    def _wait_for_rate_limit(self):
        """Enforce minimum delay between requests."""
        if self.rate_limit_delay > 0:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.rate_limit_delay:
                time.sleep(self.rate_limit_delay - elapsed)

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        timeout: int = 30,
        **kwargs,
    ) -> Optional[requests.Response]:
        """Make a single HTTP request with auth headers.

        Args:
            method: 'GET' or 'POST'
            endpoint: URL path (appended to base_url) or full URL
            params: Query parameters
            json_data: JSON body for POST requests
            timeout: Request timeout in seconds

        Returns:
            Response object, or None on failure.
        """
        self._wait_for_rate_limit()

        if endpoint.startswith("http"):
            url = endpoint  # Full URL passed (e.g., pagination next link)
        else:
            url = f"{self.base_url}/{endpoint.lstrip('/')}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                params=params,
                json=json_data,
                timeout=timeout,
                **kwargs,
            )
            self._last_request_time = time.time()
            return response
        except requests.exceptions.RequestException as e:
            log.error(f"Request error ({method} {url}): {e}")
            return None

    def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        max_retries: int = 5,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        timeout: int = 30,
        **kwargs,
    ) -> Optional[requests.Response]:
        """Make an HTTP request with exponential backoff on 429s.

        Retry logic extracted from dashboard/data_layer.py.

        Args:
            method: 'GET' or 'POST'
            endpoint: URL path or full URL
            max_retries: Maximum retry attempts
            params: Query parameters
            json_data: JSON body for POST requests
            timeout: Request timeout in seconds

        Returns:
            Response object, or None after max retries.
        """
        for attempt in range(max_retries):
            resp = self._request(
                method, endpoint, params=params, json_data=json_data,
                timeout=timeout, **kwargs
            )

            if resp is None:
                wait = min(2 ** attempt * 10, 120)
                log.warning(f"Request failed. Retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue

            if resp.status_code == 200:
                return resp

            if resp.status_code == 429:
                # Rate limited — exponential backoff
                wait = min(2 ** attempt * 15, 180)
                log.warning(f"Rate limited (429). Waiting {wait}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue

            if resp.status_code in (401, 403):
                log.error(f"Auth error ({resp.status_code}): {resp.text[:200]}")
                return None  # Don't retry auth failures

            log.error(f"HTTP {resp.status_code}: {resp.text[:300]}")
            if resp.status_code >= 500:
                # Server error — retry
                wait = min(2 ** attempt * 5, 60)
                time.sleep(wait)
                continue

            return None  # 4xx errors (except 429) — don't retry

        log.error(f"Max retries ({max_retries}) reached for {endpoint}")
        return None

    def get(self, endpoint: str, params: Optional[dict] = None, **kwargs) -> Optional[dict]:
        """Convenience: GET request with retry, returns parsed JSON or None."""
        resp = self._request_with_retry("GET", endpoint, params=params, **kwargs)
        if resp and resp.status_code == 200:
            return resp.json()
        return None

    def post(self, endpoint: str, json_data: Optional[dict] = None, **kwargs) -> Optional[dict]:
        """Convenience: POST request with retry, returns parsed JSON or None."""
        resp = self._request_with_retry("POST", endpoint, json_data=json_data, **kwargs)
        if resp and resp.status_code == 200:
            return resp.json()
        return None
