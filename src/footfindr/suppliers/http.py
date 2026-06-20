"""Shared HTTP client for supplier API integrations.

Provides timeout, retry/backoff for 429/5xx, Retry-After parsing,
secret redaction in logs, and debug mode support.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

import httpx

from footfindr import __version__

logger = logging.getLogger("footfindr.suppliers.http")

_USER_AGENT = f"FootFindr/{__version__}"
_DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)
_MAX_RETRIES = 3
_BASE_BACKOFF = 1.0  # seconds

# Patterns to redact in debug output
_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(client[_-]?secret[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(access[_-]?token[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(refresh[_-]?token[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(X-DIGIKEY-Client-Id[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(apiKey[=:]\s*)\S+", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Redact known secret patterns from text."""
    result = text
    for pat in _SECRET_PATTERNS:
        result = pat.sub(r"\1[REDACTED]", result)
    return result


class SupplierHTTPError(Exception):
    """Error from a supplier API call."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        endpoint: str = "",
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class SupplierAuthError(SupplierHTTPError):
    """Authentication/authorization error from a supplier."""
    pass


class SupplierRateLimitError(SupplierHTTPError):
    """Rate limit error from a supplier."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        self.retry_after = retry_after
        super().__init__(message, **kwargs)


class SupplierHTTPClient:
    """HTTP client wrapper for supplier APIs with retry/backoff."""

    def __init__(
        self,
        *,
        provider_name: str = "",
        base_url: str = "",
        timeout: httpx.Timeout | None = None,
        headers: dict[str, str] | None = None,
        debug: bool = False,
    ) -> None:
        self.provider_name = provider_name
        self.debug = debug
        self._base_url = base_url

        default_headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        }
        if headers:
            default_headers.update(headers)

        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout or _DEFAULT_TIMEOUT,
            headers=default_headers,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        data: Any = None,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
        max_retries: int = _MAX_RETRIES,
        retry_on_401: bool = False,
        on_401: Any = None,  # callback for token refresh
    ) -> httpx.Response:
        """Make an HTTP request with retry/backoff.

        Retries on 429 and 5xx. Does NOT retry on 400/403.
        Optionally retries once on 401 with token refresh callback.
        """
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                if self.debug:
                    self._log_request(method, url, params, headers)

                resp = self._client.request(
                    method,
                    url,
                    json=json,
                    data=data,
                    params=params,
                    headers=headers,
                )

                if self.debug:
                    self._log_response(resp)

                # Success
                if resp.status_code < 400:
                    return resp

                # 401 — try token refresh once
                if resp.status_code == 401 and retry_on_401 and on_401 and attempt == 0:
                    logger.debug(f"[{self.provider_name}] 401 — refreshing token")
                    on_401()
                    continue

                # 403 — do not retry
                if resp.status_code == 403:
                    raise SupplierAuthError(
                        f"{self.provider_name}: Access forbidden (403). "
                        "Check your API credentials and permissions.",
                        provider=self.provider_name,
                        endpoint=url,
                        status_code=403,
                    )

                # 429 — rate limit, respect Retry-After
                if resp.status_code == 429:
                    retry_after = self._parse_retry_after(resp)
                    if attempt < max_retries:
                        wait = retry_after or self._backoff(attempt)
                        logger.warning(
                            f"[{self.provider_name}] Rate limited (429). "
                            f"Waiting {wait:.1f}s..."
                        )
                        time.sleep(wait)
                        continue
                    raise SupplierRateLimitError(
                        f"{self.provider_name}: Rate limited (429). "
                        f"Max retries ({max_retries}) exhausted.",
                        provider=self.provider_name,
                        endpoint=url,
                        status_code=429,
                        retry_after=retry_after,
                    )

                # 5xx — transient, retry with backoff
                if resp.status_code >= 500:
                    if attempt < max_retries:
                        wait = self._backoff(attempt)
                        logger.warning(
                            f"[{self.provider_name}] Server error ({resp.status_code}). "
                            f"Retry {attempt + 1}/{max_retries} in {wait:.1f}s..."
                        )
                        time.sleep(wait)
                        continue
                    raise SupplierHTTPError(
                        f"{self.provider_name}: Server error ({resp.status_code})",
                        provider=self.provider_name,
                        endpoint=url,
                        status_code=resp.status_code,
                        response_body=resp.text[:500] if resp.text else None,
                    )

                # 4xx other — do not retry
                raise SupplierHTTPError(
                    f"{self.provider_name}: HTTP {resp.status_code}",
                    provider=self.provider_name,
                    endpoint=url,
                    status_code=resp.status_code,
                    response_body=resp.text[:500] if resp.text else None,
                )

            except httpx.TimeoutException as e:
                last_exc = e
                if attempt < max_retries:
                    wait = self._backoff(attempt)
                    logger.warning(
                        f"[{self.provider_name}] Timeout. "
                        f"Retry {attempt + 1}/{max_retries} in {wait:.1f}s..."
                    )
                    time.sleep(wait)
                    continue
                raise SupplierHTTPError(
                    f"{self.provider_name}: Request timed out after {max_retries} retries",
                    provider=self.provider_name,
                    endpoint=url,
                ) from e

            except httpx.HTTPError as e:
                last_exc = e
                if attempt < max_retries:
                    wait = self._backoff(attempt)
                    time.sleep(wait)
                    continue
                raise SupplierHTTPError(
                    f"{self.provider_name}: Network error: {e}",
                    provider=self.provider_name,
                    endpoint=url,
                ) from e

        # Should not reach here
        raise SupplierHTTPError(
            f"{self.provider_name}: Request failed after {max_retries} retries",
            provider=self.provider_name,
        ) from last_exc

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter."""
        base = _BASE_BACKOFF * (2 ** attempt)
        jitter = random.uniform(0, base * 0.5)
        return base + jitter

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> float | None:
        """Parse Retry-After header."""
        val = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            return None

    def _log_request(self, method: str, url: str, params: dict | None, headers: dict | None) -> None:
        full_url = f"{self._base_url}{url}" if not url.startswith("http") else url
        msg = f"[{self.provider_name}] {method} {full_url}"
        if params:
            msg += f" params={redact_secrets(str(params))}"
        if headers:
            msg += f" headers={redact_secrets(str(headers))}"
        logger.debug(msg)

    def _log_response(self, resp: httpx.Response) -> None:
        body_preview = resp.text[:200] if resp.text else ""
        logger.debug(
            f"[{self.provider_name}] Response {resp.status_code} "
            f"({len(resp.content)} bytes): {redact_secrets(body_preview)}"
        )
