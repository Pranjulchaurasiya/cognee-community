"""Read.ai REST API client for meeting summaries, transcripts, and metrics."""

import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from cognee.shared.logging_utils import get_logger

    logger = get_logger("read_ai_client")
except ImportError:
    import logging

    logger = logging.getLogger("read_ai_client")

_MAX_RETRIES = 3
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_BASE_URL = "https://api.read.ai/v1"


class ReadAIError(Exception):
    """Base exception for Read.ai connector errors."""


class ReadAIAuthError(ReadAIError):
    """Authentication or authorization failure against Read.ai API."""


class ReadAINotFoundError(ReadAIError):
    """Requested meeting report, summary, or transcript not found."""


class ReadAIClient:
    """Client for interacting with the Read.ai REST API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        verify_ssl: bool = True,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.api_key = (
            api_key
            or os.environ.get("READAI_API_KEY")
            or os.environ.get("READ_AI_API_KEY")
            or os.environ.get("READAI_TOKEN")
        )
        if not self.api_key:
            raise ValueError("Read.ai API key required: pass api_key= or set READAI_API_KEY.")

        raw_url = base_url or os.environ.get("READAI_BASE_URL") or _DEFAULT_BASE_URL
        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"Invalid Read.ai URL '{raw_url}'. Scheme and host required.")

        self.base_url = raw_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.timeout = timeout

    def redact(self, text: str) -> str:
        """Redact sensitive API key tokens from exception messages."""
        if self.api_key and self.api_key in text:
            return text.replace(self.api_key, "[REDACTED]")
        return text

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "cognee-read-ai-connector/0.1.0",
        }

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an HTTP request against Read.ai with automatic retry and backoff."""
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
                        raise ReadAIAuthError(f"Read.ai auth failed ({resp.status_code}) on {path}")
                    if resp.status_code == 404:
                        raise ReadAINotFoundError(f"Read.ai resource not found: {path}")

                    if resp.status_code in (429, 500, 502, 503, 504):
                        if attempt == _MAX_RETRIES - 1:
                            resp.raise_for_status()

                        retry_header = resp.headers.get("Retry-After")
                        try:
                            delay = float(retry_header) if retry_header else 1.5 * (2**attempt)
                        except (ValueError, TypeError):
                            delay = 1.5 * (2**attempt)

                        logger.warning(
                            "Read.ai: status %d on %s, retrying in %.1fs (%d/%d)",
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
                    raise ReadAIError(
                        f"Network error communicating with Read.ai: {clean_msg}"
                    ) from exc
                delay = 1.5 * (2**attempt)
                logger.warning(
                    "Read.ai: connection error on %s, retrying in %.1fs (%d/%d)",
                    path,
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(delay)

    def get_meetings(
        self,
        limit: int = 50,
        cursor: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a page of meeting records from Read.ai."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        data = self.request("GET", "/meetings", params=params)
        if isinstance(data, list):
            return {"meetings": data, "next_cursor": None}
        return data

    def get_meeting_summary(self, meeting_id: str) -> dict[str, Any]:
        """Fetch the executive summary, action items, and key takeaways for a meeting."""
        data = self.request("GET", f"/meetings/{meeting_id}")
        if isinstance(data, dict):
            return data
        return {}

    def get_meeting_transcript(self, meeting_id: str) -> list[dict[str, Any]]:
        """Fetch speaker-diarized transcript turns for a meeting."""
        data = self.request("GET", f"/meetings/{meeting_id}/transcript")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("transcript", []) or data.get("turns", [])
        return []
