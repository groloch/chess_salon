"""Per-user account storage backed by the app's SQLite database.

Lichess access tokens are encrypted with the ``TOKEN_ENCRYPTION_KEY`` from
the environment before being written, and are decrypted again when a row is
read, so plaintext tokens never leave this module. The web session cookie
only ever carries the local ``user_id``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid

from .auth import decrypt_token, encrypt_token
from .puzzle_history import run_migrations


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserStore:
    """Store local user accounts linked to Lichess OAuth identities."""

    def __init__(self, database_path: str | Path, encryption_key: str):
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._encryption_key = encryption_key
        with self._connect() as connection:
            run_migrations(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _entry(self, row: sqlite3.Row) -> dict:
        encrypted = row['access_token']
        return {
            'id': row['id'],
            'lichess_id': row['lichess_id'],
            'username': row['username'],
            'access_token': decrypt_token(self._encryption_key, encrypted) if encrypted else '',
            'token_type': row['token_type'],
            'expires_at': row['expires_at'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
            'last_seen_at': row['last_seen_at'],
        }

    def save_lichess_account(
        self,
        *,
        lichess_id: str,
        username: str,
        access_token: str,
        token_type: str = 'Bearer',
        expires_at: str | None = None,
    ) -> dict:
        """Insert or update the user row for a Lichess account.

        Re-authorizing the same Lichess account refreshes its stored token;
        the local user id stays stable.
        """
        now = _utc_now()
        encrypted = encrypt_token(self._encryption_key, access_token)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users
                    (id, lichess_id, username, access_token, token_type, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lichess_id) DO UPDATE SET
                    username = excluded.username,
                    access_token = excluded.access_token,
                    token_type = excluded.token_type,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (str(uuid.uuid4()), lichess_id, username, encrypted, token_type, expires_at, now, now),
            )
            row = connection.execute(
                'SELECT * FROM users WHERE lichess_id = ?', (lichess_id,)
            ).fetchone()
        return self._entry(row)

    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM users WHERE id = ?', (user_id,)
            ).fetchone()
        return self._entry(row) if row is not None else None

    def get_user_by_lichess_id(self, lichess_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM users WHERE lichess_id = ?', (lichess_id,)
            ).fetchone()
        return self._entry(row) if row is not None else None

    def touch_last_seen(self, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                'UPDATE users SET last_seen_at = ? WHERE id = ?', (_utc_now(), user_id)
            )

    def mark_token_invalid(self, user_id: str) -> None:
        """Drop the stored token after Lichess rejected it (401/403).

        An empty ``access_token`` means the user must sign in again.
        """
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET access_token = '', updated_at = ? WHERE id = ?",
                (_utc_now(), user_id),
            )

    def claim_legacy_history(self, user_id: str) -> dict[str, int]:
        """Assign rows created before auth existed (user_id NULL) to a user.

        Runs once per login; afterwards no NULL rows remain, so later users
        start with a clean history.
        """
        with self._connect() as connection:
            attempts = connection.execute(
                'UPDATE puzzle_attempts SET user_id = ? WHERE user_id IS NULL', (user_id,)
            ).rowcount
            sessions = connection.execute(
                'UPDATE puzzle_sessions SET user_id = ? WHERE user_id IS NULL', (user_id,)
            ).rowcount
        return {'attempts': attempts, 'sessions': sessions}
