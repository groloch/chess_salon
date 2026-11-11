"""Lichess OAuth2 (Authorization Code + PKCE) helpers and local auth config.

The web app never handles a client secret: it is a public client, so the
authorization code is protected with PKCE (S256) and a CSRF ``state`` value.
Access tokens are stored encrypted at rest (see ``encrypt_token``) and are
never sent to the browser — the session cookie only carries a local user id.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import secrets
from urllib.parse import urlencode

import dotenv
import requests
from cryptography.fernet import Fernet, InvalidToken

LICHESS_BASE_URL = 'https://lichess.org'
DEFAULT_REDIRECT_URI = 'http://localhost:23777/auth/callback'
DEFAULT_SCOPES = 'puzzle:read puzzle:write'
REQUEST_TIMEOUT = 15


class AuthError(Exception):
    """Raised when a step of the Lichess OAuth flow fails."""


@dataclass
class AuthConfig:
    secret_key: str
    token_encryption_key: str
    lichess_client_id: str | None
    lichess_redirect_uri: str
    lichess_scopes: str = DEFAULT_SCOPES


def load_auth_config(env_path: str = '.env') -> AuthConfig:
    """Read auth settings from the dotenv file, generating missing secrets.

    ``SECRET_KEY`` (Flask session signing) and ``TOKEN_ENCRYPTION_KEY``
    (Fernet key for access tokens at rest) are created and appended to the
    env file on first run so they survive restarts. ``LICHESS_CLIENT_ID``
    is a unique identifier chosen for this app (no registration required).
    """
    secret_key = dotenv.get_key(env_path, 'SECRET_KEY')
    if not secret_key:
        secret_key = secrets.token_hex(32)
        dotenv.set_key(env_path, 'SECRET_KEY', secret_key)
    encryption_key = dotenv.get_key(env_path, 'TOKEN_ENCRYPTION_KEY')
    if not encryption_key:
        encryption_key = Fernet.generate_key().decode('ascii')
        dotenv.set_key(env_path, 'TOKEN_ENCRYPTION_KEY', encryption_key)
    client_id = dotenv.get_key(env_path, 'LICHESS_CLIENT_ID') or None
    redirect_uri = dotenv.get_key(env_path, 'LICHESS_REDIRECT_URI') or DEFAULT_REDIRECT_URI
    scopes = dotenv.get_key(env_path, 'LICHESS_SCOPES') or DEFAULT_SCOPES
    return AuthConfig(
        secret_key=secret_key,
        token_encryption_key=encryption_key,
        lichess_client_id=client_id,
        lichess_redirect_uri=redirect_uri,
        lichess_scopes=scopes,
    )


# ---------------------------------------------------------------------------
# Token encryption at rest
# ---------------------------------------------------------------------------

def encrypt_token(key: str, token: str) -> str:
    return Fernet(key).encrypt(token.encode('utf-8')).decode('ascii')


def decrypt_token(key: str, encrypted: str) -> str:
    try:
        return Fernet(key).decrypt(encrypted.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError) as error:
        raise AuthError('Stored access token could not be decrypted.') from error


# ---------------------------------------------------------------------------
# PKCE + authorization URL
# ---------------------------------------------------------------------------

def generate_pkce_pair() -> tuple[str, str]:
    """Return a (code_verifier, code_challenge) pair for PKCE S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    return verifier, challenge


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    code_challenge: str,
    base_url: str = LICHESS_BASE_URL,
) -> str:
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': scopes,
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    }
    return f'{base_url}/oauth?{urlencode(params)}'


# ---------------------------------------------------------------------------
# Token endpoint / account API
# ---------------------------------------------------------------------------

def exchange_code(
    *,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    base_url: str = LICHESS_BASE_URL,
) -> dict:
    """Exchange an authorization code for an access token."""
    response = requests.post(
        f'{base_url}/api/token',
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'code_verifier': code_verifier,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise AuthError(f'Lichess token exchange failed (HTTP {response.status_code}).')
    payload = response.json()
    if not payload.get('access_token'):
        raise AuthError('Lichess token exchange returned no access token.')
    return payload


def fetch_account(access_token: str, base_url: str = LICHESS_BASE_URL) -> dict:
    """Fetch the Lichess account profile for an access token."""
    response = requests.get(
        f'{base_url}/api/account',
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise AuthError(f'Lichess account request failed (HTTP {response.status_code}).')
    return response.json()


def revoke_token(access_token: str, base_url: str = LICHESS_BASE_URL) -> None:
    """Revoke an access token on Lichess (used on logout)."""
    response = requests.post(
        f'{base_url}/api/token',
        data={'token': access_token},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise AuthError(f'Lichess token revocation failed (HTTP {response.status_code}).')
