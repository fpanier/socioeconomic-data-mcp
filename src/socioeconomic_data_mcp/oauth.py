"""Minimal self-contained OAuth 2.1 Authorization Server for the MCP connector.

claude.ai's custom-connector UI authenticates remote MCP servers via OAuth only
(no static-token field). This implements the MCP authorization flow so claude.ai
(web/mobile) can connect: RFC 7591 dynamic client registration + PKCE
authorization-code grant, gated by a single shared password (env
``MCP_OAUTH_PASSWORD``).

Division of labour: the MCP SDK's handlers do PKCE verification, code expiry,
redirect-uri matching and client authentication. This provider only stores
clients/codes/tokens and gates ``/authorize`` with a login page (see server.py).

A static admin token (env ``MCP_AUTH_TOKEN``) is also accepted by
``load_access_token`` so header-based clients (Claude Code/Desktop, scripts) keep
working alongside the browser OAuth flow.

Durable state (registered clients + tokens) is persisted to a JSON file when a
state path is given, so a service restart doesn't force claude.ai to
re-register / re-authorize. Authorization codes and pending logins are short-lived
and kept in memory only.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from . import notify

logger = logging.getLogger("socioeconomic_data_mcp.oauth")

AUTH_CODE_TTL = 300              # 5 min
ACCESS_TTL = 3600               # 1 h
REFRESH_TTL = 60 * 60 * 24 * 30  # 30 days
LOGIN_TTL = 600                 # pending-login window (10 min)
LOGIN_PATH = "/oauth/login"


def _now() -> int:
    return int(time.time())


class MCPOAuthProvider:
    """Implements mcp.server.auth.provider.OAuthAuthorizationServerProvider."""

    def __init__(self, *, password: str, admin_token: str | None = None, state_path: str | None = None) -> None:
        self._password = password
        self._admin_token = (admin_token or "").strip() or None
        self._state_path = Path(state_path) if state_path else None
        self._lock = threading.Lock()
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._access: dict[str, AccessToken] = {}
        self._refresh: dict[str, RefreshToken] = {}
        self._codes: dict[str, AuthorizationCode] = {}                    # ephemeral
        self._pending: dict[str, tuple[str, AuthorizationParams, int]] = {}  # txn -> (client_id, params, created)
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence (clients + tokens only; codes/pending stay in memory)
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self._state_path or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
        except (OSError, ValueError):
            logger.warning("Could not read OAuth state at %s; starting empty", self._state_path)
            return
        for cid, c in data.get("clients", {}).items():
            try:
                self._clients[cid] = OAuthClientInformationFull.model_validate(c)
            except Exception:  # noqa: BLE001 - skip a single corrupt entry
                continue
        for tok, a in data.get("access", {}).items():
            try:
                self._access[tok] = AccessToken(**a)
            except Exception:  # noqa: BLE001
                continue
        for tok, r in data.get("refresh", {}).items():
            try:
                self._refresh[tok] = RefreshToken(**r)
            except Exception:  # noqa: BLE001
                continue

    def _save_locked(self) -> None:
        if not self._state_path:
            return
        data = {
            "clients": {cid: c.model_dump(mode="json") for cid, c in self._clients.items()},
            "access": {t: a.model_dump(mode="json") for t, a in self._access.items()},
            "refresh": {t: r.model_dump(mode="json") for t, r in self._refresh.items()},
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self._state_path)
        try:
            os.chmod(self._state_path, 0o600)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # Clients (dynamic registration)
    # ------------------------------------------------------------------ #
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with self._lock:
            is_new = client_info.client_id not in self._clients
            self._clients[client_info.client_id] = client_info
            self._save_locked()
        if is_new:
            notify.send_async(
                "New registration — Socio-Economic Data MCP connector",
                "A new client just registered on the connector.\n\n"
                f"client_id:     {client_info.client_id}\n"
                f"client_name:   {getattr(client_info, 'client_name', None)}\n"
                f"redirect_uris: {getattr(client_info, 'redirect_uris', None)}\n"
                f"time (UTC):    {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
            )

    # ------------------------------------------------------------------ #
    # Authorize + password login gate
    # ------------------------------------------------------------------ #
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self._prune()
        txn = secrets.token_urlsafe(24)
        self._pending[txn] = (client.client_id, params, _now())
        return f"{LOGIN_PATH}?txn={txn}"

    def login_is_valid_txn(self, txn: str) -> bool:
        item = self._pending.get(txn)
        return bool(item and _now() - item[2] <= LOGIN_TTL)

    def check_password(self, password: str) -> bool:
        return hmac.compare_digest(password or "", self._password)

    def complete_login(self, txn: str) -> str | None:
        """Consume a pending login and mint an auth code. Returns the redirect URL
        back to the client (with code+state), or None if the txn is gone/expired."""
        item = self._pending.pop(txn, None)
        if not item:
            return None
        client_id, params, created = item
        if _now() - created > LOGIN_TTL:
            return None
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=_now() + AUTH_CODE_TTL,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    # ------------------------------------------------------------------ #
    # Code / token exchange (SDK verifies PKCE, expiry, redirect, client)
    # ------------------------------------------------------------------ #
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        return code if code and code.client_id == client.client_id else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)  # one-time use
        return self._issue(client.client_id, authorization_code.scopes)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        rt = self._refresh.get(refresh_token)
        return rt if rt and rt.client_id == client.client_id else None

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        with self._lock:
            self._refresh.pop(refresh_token.token, None)  # rotate
            self._save_locked()
        return self._issue(client.client_id, scopes or refresh_token.scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        # Static admin token: always valid (header-based clients like Claude Code).
        if self._admin_token and hmac.compare_digest(token, self._admin_token):
            return AccessToken(token=token, client_id="admin-static", scopes=[], expires_at=None)
        at = self._access.get(token)
        if at and (at.expires_at is None or at.expires_at > _now()):
            return at
        if at:  # expired
            with self._lock:
                self._access.pop(token, None)
                self._save_locked()
        return None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        tok = getattr(token, "token", None)
        if not tok:
            return
        with self._lock:
            self._access.pop(tok, None)
            self._refresh.pop(tok, None)
            self._save_locked()

    # ------------------------------------------------------------------ #
    def _issue(self, client_id: str, scopes: list[str]) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        with self._lock:
            self._access[access] = AccessToken(
                token=access, client_id=client_id, scopes=list(scopes), expires_at=_now() + ACCESS_TTL
            )
            self._refresh[refresh] = RefreshToken(
                token=refresh, client_id=client_id, scopes=list(scopes), expires_at=_now() + REFRESH_TTL
            )
            self._save_locked()
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh,
        )

    def _prune(self) -> None:
        now = _now()
        self._pending = {k: v for k, v in self._pending.items() if now - v[2] <= LOGIN_TTL}
        self._codes = {k: v for k, v in self._codes.items() if v.expires_at > now}
