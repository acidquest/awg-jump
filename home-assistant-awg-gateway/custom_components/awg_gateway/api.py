"""API client for AWG Gateway."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import ssl
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import API_TIMEOUT_SECONDS


class AwgGatewayError(Exception):
    """Base API error."""


class AwgGatewayCannotConnectError(AwgGatewayError):
    """Connection error."""


class AwgGatewayInvalidAuthError(AwgGatewayError):
    """Authentication error."""


class AwgGatewayApiDisabledError(AwgGatewayError):
    """API access is disabled or forbidden."""


class AwgGatewayControlDisabledError(AwgGatewayError):
    """Control mode is disabled."""


class AwgGatewayUnexpectedResponseError(AwgGatewayError):
    """Gateway returned an invalid response."""


@dataclass(slots=True)
class AwgGatewayClient:
    """Thin async client for the key-based gateway API."""

    session: ClientSession
    host: str
    port: int
    api_key: str
    use_https: bool = True
    verify_ssl: bool = True

    @property
    def base_url(self) -> str:
        """Return the API base URL."""
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.host}:{self.port}/api/access"

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    def _ssl_context(self) -> ssl.SSLContext | bool | None:
        if not self.use_https:
            return None
        if self.verify_ssl:
            return True
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def async_get_status(self) -> dict[str, Any]:
        """Fetch gateway telemetry."""
        payload = await self._request("GET", "/status")
        if not isinstance(payload, dict):
            raise AwgGatewayUnexpectedResponseError("Status payload must be a JSON object")
        return payload

    async def async_get_devices(self, scope: str) -> dict[str, Any]:
        """Fetch tracked devices."""
        payload = await self._request("GET", "/devices", params={"scope": scope})
        if not isinstance(payload, dict):
            raise AwgGatewayUnexpectedResponseError("Devices payload must be a JSON object")
        return payload

    async def async_set_tunnel(self, enabled: bool) -> dict[str, Any]:
        """Set tunnel enabled state."""
        payload = await self._request("POST", "/control/tunnel", json={"enabled": enabled})
        if not isinstance(payload, dict):
            raise AwgGatewayUnexpectedResponseError("Tunnel control payload must be a JSON object")
        return payload

    async def async_set_kill_switch(self, enabled: bool) -> dict[str, Any]:
        """Set kill switch enabled state."""
        payload = await self._request("POST", "/control/kill-switch", json={"enabled": enabled})
        if not isinstance(payload, dict):
            raise AwgGatewayUnexpectedResponseError("Kill switch payload must be a JSON object")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with asyncio.timeout(API_TIMEOUT_SECONDS):
                response = await self.session.request(
                    method,
                    url,
                    headers=self._headers,
                    params=params,
                    json=json,
                    ssl=self._ssl_context(),
                )
        except (TimeoutError, asyncio.TimeoutError, ClientError) as err:
            raise AwgGatewayCannotConnectError from err

        return await self._handle_response(response)

    async def _handle_response(self, response: ClientResponse) -> Any:
        text = await response.text()
        try:
            payload = await response.json(content_type=None)
        except Exception:
            payload = None

        if response.status == 401:
            raise AwgGatewayInvalidAuthError(payload or text or "Invalid API key")

        if response.status == 403:
            detail = self._detail(payload, text)
            if "control mode" in detail.lower():
                raise AwgGatewayControlDisabledError(detail)
            raise AwgGatewayApiDisabledError(detail)

        if response.status >= 400:
            raise AwgGatewayUnexpectedResponseError(self._detail(payload, text))

        return payload if payload is not None else text

    @staticmethod
    def _detail(payload: Any, text: str) -> str:
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str):
                return detail
        return text or "Unexpected gateway response"
