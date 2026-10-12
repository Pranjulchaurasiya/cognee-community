"""Grain REST API client for meeting recordings, highlights, and transcripts."""

import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from cognee.shared.logging_utils import get_logger

    logger = get_logger("grain_client")
except ImportError:
    import logging

    logger = logging.getLogger("grain_client")

_MAX_RETRIES = 3
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_BASE_URL = "https://api.grain.com/v1"


class GrainError(Exception):
    """Base exception for Grain connector errors."""


class GrainAuthError(GrainError):
    """Authentication or authorization failure against Grain API."""


class GrainNotFoundError(GrainError):
    """Requested recording, highlight, or transcript not found."""


class GrainClient:
    """Client for interacting with the Grain AI REST API."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        verify_ssl: bool = True,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.token = token or os.environ.get("GRAIN_API_TOKEN") or os.environ.get("GRAIN_TOKEN")
        if not self.token:
            raise ValueError("Grain API token required: pass token= or set GRAIN_API_TOKEN.")

        raw_url = base_url or os.environ.get("GRAIN_BASE_URL") or _DEFAULT_BASE_URL
        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"Invalid Grain URL '{raw_url}'. Scheme and host required.")

        self.base_url = raw_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.timeout = timeout

    def redact(self, text: str) -> str:
        """Redact sensitive token strings from exception messages."""
        if self.token and self.token in text:
            return text.replace(self.token, "[REDACTED]")
        return text

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "cognee-grain-connector/0.1.0",
        }

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an HTTP request against Grain with automatic retry and backoff."""
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"

        for attempt in range(_MAX_RETRIES):
            try:
                with httpx.Client(verify=self.verify_ssl, timeout=self.timeout) as client:
                    resp = client.request(
                        method=method,
                        url=url,
                        headers=self._headers(),
                        params=params,
                        json=json_data,
                    )

                    if resp.status_code in (401, 403):
                        raise GrainAuthError(f"Grain auth failed ({resp.status_code}) on {path}")
                    if resp.status_code == 404:
                        raise GrainNotFoundError(f"Grain resource not found: {path}")

                    if resp.status_code in (429, 500, 502, 503, 504):
                        if attempt == _MAX_RETRIES - 1:
                            resp.raise_for_status()

                        retry_header = resp.headers.get("Retry-After")
                        try:
                            delay = float(retry_header) if retry_header else 1.5 * (2**attempt)
                        except (ValueError, TypeError):
                            delay = 1.5 * (2**attempt)

                        logger.warning(
                            "Grain: status %d on %s, retrying in %.1fs (%d/%d)",
                            resp.status_code,
                            path,
                            delay,
                            attempt + 1,
                            _MAX_RETRIES,
                        )
                        time.sleep(delay)
                        continue

                    resp.raise_for_status()
                    if resp.content:
                        return resp.json()
                    return {}

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == _MAX_RETRIES - 1:
                    clean_msg = self.redact(str(exc))
                    raise GrainError(
                        f"Network error communicating with Grain: {clean_msg}"
                    ) from exc
                delay = 1.5 * (2**attempt)
                logger.warning(
                    "Grain: connection error on %s, retrying in %.1fs (%d/%d)",
                    path,
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(delay)

    def get_recordings(
        self,
        limit: int = 50,
        cursor: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a page of recordings/meetings from Grain."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if since:
            params["since"] = since

        data = self.request("GET", "/recordings", params=params)
        if isinstance(data, list):
            return {"recordings": data, "next_cursor": None}
        return data

    def get_highlights(self, recording_id: str) -> list[dict[str, Any]]:
        """Fetch human-curated highlights for a recording."""
        data = self.request("GET", f"/recordings/{recording_id}/highlights")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("highlights", [])
        return []

    def get_transcript(self, recording_id: str) -> list[dict[str, Any]]:
        """Fetch speaker-diarized transcript turns for a recording."""
        data = self.request("GET", f"/recordings/{recording_id}/transcript")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("transcript", []) or data.get("turns", [])
        return []
