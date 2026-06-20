"""Supplier credential and OAuth token management.

Reads credentials from environment variables. Optionally loads from
``.footfindr/.env``. Manages short-lived OAuth tokens at
``.footfindr/auth/tokens.json`` — never stores long-lived secrets there.

Environment variables:
    FOOTFINDR_MOUSER_PART_API_KEY
    FOOTFINDR_NEXAR_CLIENT_ID
    FOOTFINDR_NEXAR_CLIENT_SECRET
    FOOTFINDR_JLCPCB_APP_ID
    FOOTFINDR_JLCPCB_ACCESS_KEY
    FOOTFINDR_JLCPCB_PRIVATE_KEY
    FOOTFINDR_DIGIKEY_CLIENT_ID
    FOOTFINDR_DIGIKEY_CLIENT_SECRET
    FOOTFINDR_DIGIKEY_SANDBOX
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("footfindr.suppliers.auth")


def _load_dotenv(workspace: Path | None = None) -> None:
    """Load .footfindr/.env if it exists. Simple key=value parser."""
    if workspace is None:
        from footfindr.config import get_workspace
        workspace = get_workspace()
    env_path = workspace / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Only set if not already in environment (env vars take priority)
            if key not in os.environ:
                os.environ[key] = value
    except Exception as e:
        logger.debug(f"Could not load .env: {e}")


# ---------------------------------------------------------------------------
# Credential dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DigiKeyCredentials:
    client_id: str
    client_secret: str
    sandbox: bool = False
    callback_url: str | None = None

    @staticmethod
    def from_env() -> Optional[DigiKeyCredentials]:
        _load_dotenv()
        cid = os.environ.get("FOOTFINDR_DIGIKEY_CLIENT_ID")
        csec = os.environ.get("FOOTFINDR_DIGIKEY_CLIENT_SECRET")
        if not cid or not csec:
            return None
        sandbox = os.environ.get("FOOTFINDR_DIGIKEY_SANDBOX", "0") in ("1", "true", "yes")
        callback_url = os.environ.get("FOOTFINDR_DIGIKEY_CALLBACK_URL")
        return DigiKeyCredentials(
            client_id=cid, client_secret=csec,
            sandbox=sandbox, callback_url=callback_url,
        )


@dataclass
class MouserCredentials:
    part_api_key: str

    @staticmethod
    def from_env() -> Optional[MouserCredentials]:
        _load_dotenv()
        key = os.environ.get("FOOTFINDR_MOUSER_PART_API_KEY")
        if not key:
            return None
        return MouserCredentials(part_api_key=key)


@dataclass
class NexarCredentials:
    client_id: str
    client_secret: str

    @staticmethod
    def from_env() -> Optional[NexarCredentials]:
        _load_dotenv()
        cid = os.environ.get("FOOTFINDR_NEXAR_CLIENT_ID")
        csec = os.environ.get("FOOTFINDR_NEXAR_CLIENT_SECRET")
        if not cid or not csec:
            return None
        return NexarCredentials(client_id=cid, client_secret=csec)


@dataclass
class JLCPCBCredentials:
    app_id: str
    access_key: str
    private_key: str | None = None

    @staticmethod
    def from_env() -> Optional[JLCPCBCredentials]:
        _load_dotenv()
        app_id = os.environ.get("FOOTFINDR_JLCPCB_APP_ID")
        access_key = os.environ.get("FOOTFINDR_JLCPCB_ACCESS_KEY")
        if not app_id or not access_key:
            return None
        private_key = os.environ.get("FOOTFINDR_JLCPCB_PRIVATE_KEY")
        return JLCPCBCredentials(
            app_id=app_id,
            access_key=access_key,
            private_key=private_key,
        )


# ---------------------------------------------------------------------------
# Provider-specific auth status
# ---------------------------------------------------------------------------

@dataclass
class AuthStatus:
    """Status of provider authentication."""
    provider: str
    configured: bool
    env_vars_present: list[str]
    env_vars_missing: list[str]
    sandbox: bool = False
    token_valid: bool = False
    token_expires: str | None = None
    error: str | None = None


def digikey_auth_status() -> AuthStatus:
    _load_dotenv()
    present = []
    missing = []
    for var in ["FOOTFINDR_DIGIKEY_CLIENT_ID", "FOOTFINDR_DIGIKEY_CLIENT_SECRET"]:
        if os.environ.get(var):
            present.append(var)
        else:
            missing.append(var)
    sandbox = os.environ.get("FOOTFINDR_DIGIKEY_SANDBOX", "0") in ("1", "true", "yes")
    return AuthStatus(
        provider="digikey",
        configured=len(missing) == 0,
        env_vars_present=present,
        env_vars_missing=missing,
        sandbox=sandbox,
    )


def mouser_auth_status() -> AuthStatus:
    _load_dotenv()
    present = []
    missing = []
    var = "FOOTFINDR_MOUSER_PART_API_KEY"
    if os.environ.get(var):
        present.append(var)
    else:
        missing.append(var)
    return AuthStatus(
        provider="mouser",
        configured=len(missing) == 0,
        env_vars_present=present,
        env_vars_missing=missing,
    )


def nexar_auth_status() -> AuthStatus:
    _load_dotenv()
    present = []
    missing = []
    for var in ["FOOTFINDR_NEXAR_CLIENT_ID", "FOOTFINDR_NEXAR_CLIENT_SECRET"]:
        if os.environ.get(var):
            present.append(var)
        else:
            missing.append(var)
    return AuthStatus(
        provider="nexar",
        configured=len(missing) == 0,
        env_vars_present=present,
        env_vars_missing=missing,
    )


def jlcpcb_auth_status() -> AuthStatus:
    _load_dotenv()
    present = []
    missing = []
    for var in ["FOOTFINDR_JLCPCB_APP_ID", "FOOTFINDR_JLCPCB_ACCESS_KEY"]:
        if os.environ.get(var):
            present.append(var)
        else:
            missing.append(var)
    return AuthStatus(
        provider="jlcpcb",
        configured=len(missing) == 0,
        env_vars_present=present,
        env_vars_missing=missing,
    )


# ---------------------------------------------------------------------------
# OAuth token manager (for Nexar — client_credentials)
# ---------------------------------------------------------------------------

@dataclass
class OAuthToken:
    access_token: str
    expires_at: float  # unix timestamp
    token_type: str = "Bearer"
    scope: str = ""


class OAuthTokenManager:
    """Manages OAuth2 client_credentials tokens with caching."""

    def __init__(
        self,
        *,
        provider_name: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str = "",
        workspace: Path | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self._token: OAuthToken | None = None

        if workspace is None:
            from footfindr.config import get_workspace
            workspace = get_workspace()
        self._token_dir = workspace / "auth"
        self._token_file = self._token_dir / "tokens.json"

        # Try to load cached token
        self._load_cached_token()

    def get_token(self) -> str:
        """Get a valid access token, refreshing if needed."""
        if self._token and self._token.expires_at > time.time() + 60:
            return self._token.access_token
        self._refresh_token()
        return self._token.access_token

    def _refresh_token(self) -> None:
        """Request a new token from the OAuth endpoint."""
        logger.debug(f"[{self.provider_name}] Requesting new OAuth token")
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.scope:
            data["scope"] = self.scope

        try:
            resp = httpx.post(
                self.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"{self.provider_name}: OAuth token request failed "
                f"({e.response.status_code}). Check client_id and client_secret."
            ) from e
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"{self.provider_name}: OAuth token request failed: {e}"
            ) from e

        body = resp.json()
        expires_in = body.get("expires_in", 3600)

        self._token = OAuthToken(
            access_token=body["access_token"],
            expires_at=time.time() + expires_in,
            token_type=body.get("token_type", "Bearer"),
            scope=body.get("scope", self.scope),
        )
        self._save_cached_token()

    def invalidate(self) -> None:
        """Force token refresh on next call."""
        self._token = None

    def _load_cached_token(self) -> None:
        """Load token from disk cache."""
        if not self._token_file.exists():
            return
        try:
            data = json.loads(self._token_file.read_text(encoding="utf-8"))
            provider_data = data.get(self.provider_name)
            if not provider_data:
                return
            token = OAuthToken(
                access_token=provider_data["access_token"],
                expires_at=provider_data["expires_at"],
                token_type=provider_data.get("token_type", "Bearer"),
                scope=provider_data.get("scope", ""),
            )
            if token.expires_at > time.time() + 60:
                self._token = token
                logger.debug(f"[{self.provider_name}] Loaded cached token (expires {token.expires_at})")
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    def _save_cached_token(self) -> None:
        """Save token to disk cache."""
        if not self._token:
            return
        self._token_dir.mkdir(parents=True, exist_ok=True)

        data = {}
        if self._token_file.exists():
            try:
                data = json.loads(self._token_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, TypeError):
                pass

        data[self.provider_name] = {
            "access_token": self._token.access_token,
            "expires_at": self._token.expires_at,
            "token_type": self._token.token_type,
            "scope": self._token.scope,
            "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._token_file.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Self-signed certificate helper (no external openssl needed)
# ---------------------------------------------------------------------------

def _generate_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """Generate a self-signed localhost certificate using pure Python.

    Uses the `cryptography` library if available (pip install cryptography),
    otherwise falls back to running `openssl` CLI.
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ])

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
            .not_valid_after(
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )

        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        logger.debug("Generated self-signed localhost certificate (cryptography lib)")

    except ImportError:
        # Fallback: try openssl CLI
        import subprocess
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "365", "-nodes",
            "-subj", "/CN=localhost",
        ], capture_output=True, check=True)
        logger.debug("Generated self-signed localhost certificate (openssl CLI)")


# ---------------------------------------------------------------------------
# DigiKey OAuth manager (Authorization Code flow)
# ---------------------------------------------------------------------------

_DIGIKEY_AUTH_URL = "https://api.digikey.com/v1/oauth2/authorize"
_DIGIKEY_TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
_DIGIKEY_SANDBOX_AUTH_URL = "https://sandbox-api.digikey.com/v1/oauth2/authorize"
_DIGIKEY_SANDBOX_TOKEN_URL = "https://sandbox-api.digikey.com/v1/oauth2/token"


class DigiKeyOAuthManager:
    """Manages DigiKey OAuth2 Authorization Code flow.

    DigiKey production API requires authorization_code grant:
    1. First time: opens browser for user to authorize, captures code via
       local HTTPS callback server, exchanges code for access + refresh tokens.
    2. Subsequent: uses cached refresh token to get new access tokens.

    Tokens are cached under key ``digikey_3leg`` in tokens.json to avoid
    collisions with the two-legged ``digikey`` key used by OAuthTokenManager.
    """

    _CACHE_KEY = "digikey_3leg"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        callback_url: str = "https://localhost:8765/digikey/oauth/callback",
        sandbox: bool = False,
        workspace: Path | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url
        self.sandbox = sandbox
        self._access_token: str | None = None
        self._refresh_token_val: str | None = None
        self._expires_at: float = 0

        if workspace is None:
            from footfindr.config import get_workspace
            workspace = get_workspace()
        self._token_dir = workspace / "auth"
        self._token_file = self._token_dir / "tokens.json"

        self._auth_url = _DIGIKEY_SANDBOX_AUTH_URL if sandbox else _DIGIKEY_AUTH_URL
        self._token_url = _DIGIKEY_SANDBOX_TOKEN_URL if sandbox else _DIGIKEY_TOKEN_URL

        self._load_cached()

    @classmethod
    def has_cached_tokens(cls, workspace: Path | None = None) -> bool:
        """Check if cached 3-legged tokens exist (access or refresh)."""
        if workspace is None:
            from footfindr.config import get_workspace
            workspace = get_workspace()
        token_file = workspace / "auth" / "tokens.json"
        if not token_file.exists():
            return False
        try:
            data = json.loads(token_file.read_text(encoding="utf-8"))
            dk = data.get(cls._CACHE_KEY)
            if not dk:
                return False
            return bool(dk.get("refresh_token") or dk.get("access_token"))
        except (json.JSONDecodeError, KeyError, TypeError):
            return False

    def get_token(self) -> str:
        """Get a valid access token."""
        # If we have a valid token, return it
        if self._access_token and self._expires_at > time.time() + 60:
            return self._access_token

        # If we have a refresh token, use it
        if self._refresh_token_val:
            try:
                self._do_refresh()
                return self._access_token
            except Exception as e:
                logger.warning(f"[digikey] Refresh token failed: {e}. Need re-authorization.")
                self._refresh_token_val = None

        # Need initial authorization
        self._do_authorization_code_flow()
        return self._access_token

    def invalidate(self) -> None:
        """Force re-authentication on next call."""
        self._access_token = None

    def do_login(
        self,
        *,
        timeout: int = 600,
        debug: bool = False,
        manual_code: bool = False,
        port: int | None = None,
    ) -> str:
        """Run OAuth login flow with full CLI options.

        Args:
            timeout: Seconds to wait for callback (default 600).
            debug: Print debug diagnostics (redacted).
            manual_code: Skip local server; prompt user to paste redirect URL.
            port: Override callback port (default from callback_url).

        Returns:
            The access token.
        """
        import urllib.parse

        # Resolve port
        parsed = urllib.parse.urlparse(self.callback_url)
        effective_port = port or parsed.port or 8765

        # If a custom port was passed and differs from the callback_url, rebuild
        if port and port != (parsed.port or 8765):
            self.callback_url = f"{parsed.scheme}://localhost:{port}{parsed.path or '/digikey/oauth/callback'}"
            print(f"\n⚠  Custom port {port}. Make sure this exact callback URL is registered")
            print(f"   in the DigiKey developer portal:")
            print(f"   {self.callback_url}\n")

        # Build authorization URL
        auth_params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.callback_url,
        }
        auth_url = f"{self._auth_url}?{urllib.parse.urlencode(auth_params)}"

        if debug:
            redacted_cid = self.client_id[:8] + "..." if len(self.client_id) > 8 else "[short]"
            print(f"[debug] callback_url: {self.callback_url}")
            print(f"[debug] port: {effective_port}")
            print(f"[debug] authorization_url: {self._auth_url}?...client_id={redacted_cid}...")
            print(f"[debug] mode: {'manual-code' if manual_code else 'callback-server'}")

        if manual_code:
            return self._do_manual_code_flow(auth_url, debug=debug)
        else:
            return self._do_server_code_flow(
                auth_url,
                port=effective_port,
                scheme=parsed.scheme or "https",
                timeout=timeout,
                debug=debug,
            )

    def _do_manual_code_flow(self, auth_url: str, *, debug: bool = False) -> str:
        """Manual code flow: user pastes redirect URL from browser."""
        import urllib.parse

        print(f"\n{'='*60}")
        print("DigiKey Authorization (Manual Code Mode)")
        print(f"{'='*60}")
        print("1. Open this URL in your browser:")
        print(f"   {auth_url}")
        print()
        print("2. Log in and authorize FootFindr.")
        print()
        print("3. After authorization, your browser will redirect to localhost.")
        print("   If it says 'localhost unreachable', that is expected.")
        print("   Copy the FULL URL from your browser's address bar.")
        print(f"{'='*60}\n")

        try:
            import webbrowser
            webbrowser.open(auth_url)
        except Exception:
            pass  # Don't fail if browser won't open

        redirect_url = input("Paste the final redirected URL: ").strip()

        if not redirect_url:
            raise RuntimeError("No URL provided. Authorization cancelled.")

        # Parse code or error from the URL
        parsed = urllib.parse.urlparse(redirect_url)
        qs = urllib.parse.parse_qs(parsed.query)

        if debug:
            print(f"[debug] callback received query keys: {list(qs.keys())}")

        error = qs.get("error", [None])[0]
        if error:
            error_desc = qs.get("error_description", [""])[0]
            raise RuntimeError(f"DigiKey authorization denied: {error} — {error_desc}")

        code = qs.get("code", [None])[0]
        if not code:
            raise RuntimeError(
                "No authorization code found in the URL.\n"
                "Expected URL format: https://localhost:.../callback?code=..."
            )

        if debug:
            print("[debug] authorization code: [REDACTED]")
            print("[debug] exchanging code for tokens...")

        self._exchange_code(code)
        return self._access_token

    def _do_server_code_flow(
        self,
        auth_url: str,
        *,
        port: int,
        scheme: str,
        timeout: int,
        debug: bool,
    ) -> str:
        """Server-based code flow: start local callback server."""
        import urllib.parse
        import webbrowser
        import ssl
        import http.server
        import threading

        captured_code: list[str] = []
        captured_error: list[str] = []

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self_handler):
                query = urllib.parse.urlparse(self_handler.path).query
                params = urllib.parse.parse_qs(query)
                code = params.get("code", [None])[0]

                if debug:
                    print(f"[debug] callback received on path: {self_handler.path.split('?')[0]}")
                    print(f"[debug] callback query keys: {list(params.keys())}")

                if code:
                    captured_code.append(code)
                    self_handler.send_response(200)
                    self_handler.send_header("Content-Type", "text/html")
                    self_handler.end_headers()
                    self_handler.wfile.write(
                        b"<html><body><h2>FootFindr: DigiKey Authorization Successful!</h2>"
                        b"<p>You can close this window and return to the terminal.</p></body></html>"
                    )
                else:
                    error = params.get("error", ["unknown"])[0]
                    captured_error.append(error)
                    self_handler.send_response(400)
                    self_handler.send_header("Content-Type", "text/html")
                    self_handler.end_headers()
                    self_handler.wfile.write(
                        f"<html><body><h2>Authorization Failed</h2><p>{error}</p></body></html>".encode()
                    )

            def log_message(self_handler, format, *args):
                if debug:
                    print(f"[debug] server: {format % args}")

        # Start HTTP(S) server
        try:
            server = http.server.HTTPServer(("localhost", port), CallbackHandler)

            if scheme == "https":
                try:
                    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    cert_dir = self._token_dir / "ssl"
                    cert_dir.mkdir(parents=True, exist_ok=True)
                    cert_path = cert_dir / "localhost.pem"
                    key_path = cert_dir / "localhost-key.pem"

                    if not cert_path.exists():
                        _generate_self_signed_cert(cert_path, key_path)

                    ssl_ctx.load_cert_chain(str(cert_path), str(key_path))
                    server.socket = ssl_ctx.wrap_socket(server.socket, server_side=True)
                    if debug:
                        print(f"[debug] server_start_success: HTTPS on port {port}")
                except Exception as ssl_err:
                    logger.warning(f"SSL setup failed ({ssl_err}), using plain HTTP")
                    if debug:
                        print(f"[debug] SSL setup failed: {ssl_err}")
                        print(f"[debug] server_start_success: HTTP (fallback) on port {port}")
            else:
                if debug:
                    print(f"[debug] server_start_success: HTTP on port {port}")

        except OSError as e:
            raise RuntimeError(
                f"Cannot start OAuth callback server on port {port}: {e}\n"
                "Ensure the port is available or use: --port <alt_port>"
            ) from e

        # Keep server alive in a loop until code/error/timeout
        server.timeout = 1  # 1-second poll intervals
        stop_event = threading.Event()

        def _serve_until_done():
            while not stop_event.is_set():
                server.handle_request()
                if captured_code or captured_error:
                    break

        server_thread = threading.Thread(target=_serve_until_done, daemon=True)
        server_thread.start()

        # Open browser
        print(f"\n{'='*60}")
        print("DigiKey Authorization Required")
        print(f"{'='*60}")
        print("Opening browser to authorize FootFindr with DigiKey...")
        print("If the browser doesn't open, visit:")
        print(f"  {auth_url}")
        print(f"\nWaiting up to {timeout} seconds for DigiKey callback...")
        print(f"{'='*60}\n")

        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

        # Wait for callback or timeout
        try:
            server_thread.join(timeout=timeout)
        except KeyboardInterrupt:
            print("\nAuthorization cancelled by user.")
            stop_event.set()
            server.server_close()
            raise RuntimeError("Authorization cancelled by user (Ctrl+C).")

        stop_event.set()
        server.server_close()

        if captured_error:
            raise RuntimeError(f"DigiKey authorization failed: {captured_error[0]}")

        if not captured_code:
            cb_url = self.callback_url
            raise RuntimeError(
                f"Timed out waiting for callback on {cb_url}.\n\n"
                "Possible causes:\n"
                f"  1. DigiKey login took longer than {timeout} seconds.\n"
                "  2. Browser is waiting on self-signed localhost certificate warning.\n"
                "  3. Callback URL in DigiKey portal does not exactly match.\n"
                "  4. Local firewall/browser blocked localhost HTTPS.\n\n"
                "Try:\n"
                f"  ff supplier auth login digikey --timeout {timeout * 2}\n"
                "  ff supplier auth login digikey --manual-code"
            )

        code = captured_code[0]

        if debug:
            print("[debug] authorization code: [REDACTED]")
            print("[debug] exchanging code for tokens...")

        self._exchange_code(code)

        if debug:
            print("[debug] token exchange: success")

        return self._access_token

    def _do_authorization_code_flow(self) -> None:
        """Legacy entry point — delegates to do_login with defaults."""
        self.do_login(timeout=600)

    def _exchange_code(self, code: str) -> None:
        """Exchange authorization code for access + refresh tokens."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.callback_url,
        }

        try:
            resp = httpx.post(
                self._token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.text[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"DigiKey token exchange failed ({e.response.status_code}): {error_body}"
            ) from e

        body = resp.json()
        self._access_token = body["access_token"]
        self._refresh_token_val = body.get("refresh_token")
        self._expires_at = time.time() + body.get("expires_in", 1800)
        self._save_cached()
        logger.info("[digikey] Authorization successful, tokens cached.")

    def _do_refresh(self) -> None:
        """Use refresh token to get a new access token."""
        logger.debug("[digikey] Refreshing access token with refresh token")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token_val,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        resp = httpx.post(
            self._token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        resp.raise_for_status()

        body = resp.json()
        self._access_token = body["access_token"]
        self._refresh_token_val = body.get("refresh_token", self._refresh_token_val)
        self._expires_at = time.time() + body.get("expires_in", 1800)
        self._save_cached()

    def _load_cached(self) -> None:
        """Load tokens from disk (key: digikey_3leg)."""
        if not self._token_file.exists():
            return
        try:
            data = json.loads(self._token_file.read_text(encoding="utf-8"))
            dk = data.get(self._CACHE_KEY)
            if not dk:
                return
            self._access_token = dk.get("access_token")
            self._refresh_token_val = dk.get("refresh_token")
            self._expires_at = dk.get("expires_at", 0)
            if self._access_token or self._refresh_token_val:
                logger.debug("[digikey] Loaded cached 3-legged tokens")
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    def _save_cached(self) -> None:
        """Save tokens to disk (key: digikey_3leg)."""
        self._token_dir.mkdir(parents=True, exist_ok=True)

        data = {}
        if self._token_file.exists():
            try:
                data = json.loads(self._token_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, TypeError):
                pass

        data[self._CACHE_KEY] = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token_val,
            "expires_at": self._expires_at,
            "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._token_file.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )


def clear_cached_tokens(workspace: Path | None = None) -> bool:
    """Clear all cached OAuth tokens."""
    if workspace is None:
        from footfindr.config import get_workspace
        workspace = get_workspace()
    token_file = workspace / "auth" / "tokens.json"
    if token_file.exists():
        token_file.unlink()
        return True
    return False

