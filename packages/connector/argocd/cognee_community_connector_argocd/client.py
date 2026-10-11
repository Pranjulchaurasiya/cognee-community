"""Argo CD API client for querying applications, projects, and cluster topologies."""

import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from cognee.shared.logging_utils import get_logger
    logger = get_logger("argocd_client")
except ImportError:
    import logging
    logger = logging.getLogger("argocd_client")

_MAX_RETRIES = 3
_DEFAULT_TIMEOUT = 30.0


class ArgoCDError(Exception):
    """Base exception for Argo CD connector errors."""


class ArgoCDAuthError(ArgoCDError):
    """Authentication or authorization failure against Argo CD API."""


class ArgoCDNotFoundError(ArgoCDError):
    """Requested resource not found in Argo CD."""


class ArgoCDClient:
    """Client for interacting with the Argo CD REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        verify_ssl: bool = True,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        raw_url = base_url or os.environ.get("ARGOCD_SERVER_URL")
        if not raw_url:
            raise ValueError(
                "Argo CD server URL required: pass base_url= or set ARGOCD_SERVER_URL."
            )

        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                f"Invalid Argo CD server URL '{raw_url}'. Must include http:// or https:// scheme and host."
            )

        self.base_url = raw_url.rstrip("/")
        self.token = token or os.environ.get("ARGOCD_AUTH_TOKEN")
        if not self.token:
            raise ValueError(
                "Argo CD authentication token required: pass token= or set ARGOCD_AUTH_TOKEN."
            )

        self.verify_ssl = verify_ssl
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _redact(self, text: str) -> str:
        if not text or not self.token:
            return text
        return text.replace(self.token, "[REDACTED]")

    def request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        """Execute HTTP request with exponential backoff on transient errors."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        
        for attempt in range(_MAX_RETRIES):
            try:
                with httpx.Client(verify=self.verify_ssl, timeout=self.timeout) as client:
                    resp = client.request(
                        method=method,
                        url=url,
                        headers=self._headers(),
                        params=params,
                    )
                    
                    if resp.status_code in (401, 403):
                        raise ArgoCDAuthError(
                            f"Argo CD authentication failed ({resp.status_code}) on {path}"
                        )
                    if resp.status_code == 404:
                        raise ArgoCDNotFoundError(f"Argo CD resource not found: {path}")
                    if resp.status_code in (429, 500, 502, 503, 504):
                        if attempt == _MAX_RETRIES - 1:
                            resp.raise_for_status()
                        delay = 1.5 * (2 ** attempt)
                        logger.warning(
                            "Argo CD: received status %d on %s — retrying in %.1fs (%d/%d)",
                            resp.status_code, path, delay, attempt + 1, _MAX_RETRIES
                        )
                        time.sleep(delay)
                        continue
                    
                    resp.raise_for_status()
                    return resp.json()

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == _MAX_RETRIES - 1:
                    raise ArgoCDError(f"Network error communicating with Argo CD: {exc}") from exc
                delay = 1.5 * (2 ** attempt)
                logger.warning(
                    "Argo CD: connection error on %s — retrying in %.1fs (%d/%d)",
                    path, delay, attempt + 1, _MAX_RETRIES
                )
                time.sleep(delay)

    def get_applications(
        self,
        projects: list[str] | None = None,
        app_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List Argo CD applications, optionally filtering by project or name."""
        params: dict[str, Any] = {}
        if projects:
            params["projects"] = projects

        data = self.request("GET", "/api/v1/applications", params=params)
        items = data.get("items") or []

        if app_names:
            target_names = set(app_names)
            items = [item for item in items if item.get("metadata", {}).get("name") in target_names]

        return items

    def get_application(self, name: str) -> dict[str, Any]:
        """Fetch details for a single Argo CD application."""
        return self.request("GET", f"/api/v1/applications/{name}")

    def get_projects(self) -> list[dict[str, Any]]:
        """List Argo CD AppProjects."""
        data = self.request("GET", "/api/v1/projects")
        return data.get("items") or []

    def get_clusters(self) -> list[dict[str, Any]]:
        """List registered Kubernetes target clusters."""
        data = self.request("GET", "/api/v1/clusters")
        return data.get("items") or []
