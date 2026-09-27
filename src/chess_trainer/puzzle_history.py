"""SQLite persistence and statistics for completed blindfold puzzles/sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3
from pathlib import Path
import uuid


class PuzzleHistoryStore:
    """Store puzzle results and timed-session metadata in local SQLite."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _utc(value: datetime | None = None) -> datetime:
        value = value or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS puzzle_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    completed_at TEXT NOT NULL,
                    puzzle_id TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    fen TEXT NOT NULL,
                    orientation TEXT NOT NULL CHECK (orientation IN ('white', 'black')),
                    moves_json TEXT NOT NULL,
                    session_id TEXT,
                    outcome TEXT NOT NULL DEFAULT 'failure'
                )
                """
            )
            attempt_columns = {
                row['name'] for row in connection.execute('PRAGMA table_info(puzzle_attempts)')
            }
            if 'session_id' not in attempt_columns:
                connection.execute('ALTER TABLE puzzle_attempts ADD COLUMN session_id TEXT')
            if 'outcome' not in attempt_columns:
                connection.execute("ALTER TABLE puzzle_attempts ADD COLUMN outcome TEXT NOT NULL DEFAULT 'failure'")
                connection.execute(
                    "UPDATE puzzle_attempts SET outcome = CASE WHEN success = 1 "
                    "THEN 'success' ELSE 'failure' END"
                )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS puzzle_sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'expired')),
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    successes INTEGER NOT NULL DEFAULT 0,
                    expired_count INTEGER NOT NULL DEFAULT 0,
                    mode TEXT NOT NULL DEFAULT 'easiest',
                    blindfold_depth INTEGER NOT NULL DEFAULT 9,
                    current_puzzle_id TEXT,
                    current_rating INTEGER,
                    current_fen TEXT,
                    current_orientation TEXT,
                    current_moves_json TEXT
                )
                """
            )
            session_columns = {
                row['name'] for row in connection.execute('PRAGMA table_info(puzzle_sessions)')
            }
            migrations = {
                'expired_count': 'INTEGER NOT NULL DEFAULT 0',
                'mode': "TEXT NOT NULL DEFAULT 'easiest'",
                'blindfold_depth': 'INTEGER NOT NULL DEFAULT 9',
                'current_puzzle_id': 'TEXT',
                'current_rating': 'INTEGER',
                'current_fen': 'TEXT',
                'current_orientation': 'TEXT',
                'current_moves_json': 'TEXT',
            }
            for name, definition in migrations.items():
                if name not in session_columns:
                    connection.execute(f'ALTER TABLE puzzle_sessions ADD COLUMN {name} {definition}')

            connection.execute(
                "CREATE INDEX IF NOT EXISTS puzzle_attempts_completed_at "
                "ON puzzle_attempts (completed_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS puzzle_attempts_session "
                "ON puzzle_attempts (session_id, completed_at)"
            )

    def _insert_attempt(
        self,
        connection: sqlite3.Connection,
        *,
        completed_at: str,
        puzzle_id: str,
        rating: int,
        outcome: str,
        fen: str,
        orientation: str,
        moves: list[dict],
        session_id: str | None,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO puzzle_attempts
                (completed_at, puzzle_id, rating, success, fen, orientation, moves_json, session_id, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                completed_at, puzzle_id, int(rating), int(outcome == 'success'), fen,
                orientation, json.dumps(moves), session_id, outcome,
            ),
        )
        return int(cursor.lastrowid)

    def record_attempt(
        self,
        *,
        puzzle_id: str,
        rating: int,
        success: bool,
        fen: str,
        orientation: str,
        moves: list[dict],
        completed_at: datetime | None = None,
        session_id: str | None = None,
    ) -> int | None:
        timestamp_value = self._utc(completed_at)
        timestamp = timestamp_value.isoformat()
        with self._connect() as connection:
            if session_id is not None:
                session = connection.execute(
                    'SELECT * FROM puzzle_sessions WHERE id = ?',
                    (session_id,),
                ).fetchone()
                if not session or session['status'] != 'active':
                    return None
                if timestamp >= session['ends_at']:
                    if session['current_puzzle_id'] is not None:
                        self._insert_attempt(
                            connection,
                            completed_at=session['ends_at'],
                            puzzle_id=session['current_puzzle_id'],
                            rating=session['current_rating'],
                            outcome='expired',
                            fen=session['current_fen'],
                            orientation=session['current_orientation'],
                            moves=json.loads(session['current_moves_json'] or '[]'),
                            session_id=session_id,
                        )
                        connection.execute(
                            "UPDATE puzzle_sessions SET status = 'expired', ended_at = ends_at, "
                            "expired_count = expired_count + 1, current_puzzle_id = NULL, "
                            "current_rating = NULL, current_fen = NULL, current_orientation = NULL, "
                            "current_moves_json = NULL WHERE id = ? AND status = 'active'",
                            (session_id,),
                        )
                    else:
                        connection.execute(
                            "UPDATE puzzle_sessions SET status = 'expired', ended_at = ends_at "
                            "WHERE id = ? AND status = 'active'",
                            (session_id,),
                        )
                    return None

            attempt_id = self._insert_attempt(
                connection,
                completed_at=timestamp,
                puzzle_id=puzzle_id,
                rating=rating,
                outcome='success' if success else 'failure',
                fen=fen,
                orientation=orientation,
                moves=moves,
                session_id=session_id,
            )
            if session_id is not None:
                connection.execute(
                    "UPDATE puzzle_sessions SET completed_count = completed_count + 1, "
                    "successes = successes + ?, current_puzzle_id = NULL, current_rating = NULL, "
                    "current_fen = NULL, current_orientation = NULL, current_moves_json = NULL "
                    "WHERE id = ?",
                    (int(success), session_id),
                )
            return attempt_id

    def start_session(
        self,
        duration_minutes: int,
        *,
        mode: str = 'easiest',
        blindfold_depth: int = 9,
        started_at: datetime | None = None,
    ) -> dict:
        started = self._utc(started_at)
        ends = started + timedelta(minutes=duration_minutes)
        session_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO puzzle_sessions "
                "(id, started_at, ends_at, duration_seconds, status, mode, blindfold_depth) "
                "VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (
                    session_id, started.isoformat(), ends.isoformat(), duration_minutes * 60,
                    mode, blindfold_depth,
                ),
            )
        return self.get_session(session_id)

    def set_session_puzzle(
        self,
        session_id: str,
        *,
        puzzle_id: str,
        rating: int,
        fen: str,
        orientation: str,
        moves: list[dict],
        now: datetime | None = None,
    ) -> bool:
        timestamp_value = self._utc(now)
        timestamp = timestamp_value.isoformat()
        self.expire_session_if_due(session_id, now=timestamp_value)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE puzzle_sessions SET current_puzzle_id = ?, current_rating = ?, "
                "current_fen = ?, current_orientation = ?, current_moves_json = ? "
                "WHERE id = ? AND status = 'active' AND ends_at > ?",
                (
                    puzzle_id, rating, fen, orientation, json.dumps(moves), session_id, timestamp,
                ),
            )
            return cursor.rowcount == 1

    def expire_session_if_due(
        self,
        session_id: str,
        now: datetime | None = None,
    ) -> dict | None:
        timestamp = self._utc(now).isoformat()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            session = connection.execute(
                'SELECT * FROM puzzle_sessions WHERE id = ?', (session_id,)
            ).fetchone()
            if session is None:
                return None
            if session['status'] == 'active' and session['ends_at'] <= timestamp:
                if session['current_puzzle_id'] is not None:
                    self._insert_attempt(
                        connection,
                        completed_at=session['ends_at'],
                        puzzle_id=session['current_puzzle_id'],
                        rating=session['current_rating'],
                        outcome='expired',
                        fen=session['current_fen'],
                        orientation=session['current_orientation'],
                        moves=json.loads(session['current_moves_json'] or '[]'),
                        session_id=session_id,
                    )
                    connection.execute(
                        'UPDATE puzzle_sessions SET expired_count = expired_count + 1, '
                        'current_puzzle_id = NULL, current_rating = NULL, current_fen = NULL, '
                        'current_orientation = NULL, current_moves_json = NULL WHERE id = ?',
                        (session_id,),
                    )
                connection.execute(
                    "UPDATE puzzle_sessions SET status = 'expired', ended_at = ends_at, "
                    "current_puzzle_id = NULL, current_rating = NULL, current_fen = NULL, "
                    "current_orientation = NULL, current_moves_json = NULL "
                    "WHERE id = ? AND status = 'active'",
                    (session_id,),
                )
                session = connection.execute(
                    'SELECT * FROM puzzle_sessions WHERE id = ?', (session_id,)
                ).fetchone()
            return self._session_entry(session)

    def end_session(self, session_id: str, now: datetime | None = None) -> dict | None:
        timestamp = self._utc(now).isoformat()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            session = connection.execute(
                'SELECT * FROM puzzle_sessions WHERE id = ?', (session_id,)
            ).fetchone()
            if session is None:
                return None
            if session['status'] == 'active':
                if session['current_puzzle_id'] is not None:
                    self._insert_attempt(
                        connection,
                        completed_at=timestamp,
                        puzzle_id=session['current_puzzle_id'],
                        rating=session['current_rating'],
                        outcome='expired',
                        fen=session['current_fen'],
                        orientation=session['current_orientation'],
                        moves=json.loads(session['current_moves_json'] or '[]'),
                        session_id=session_id,
                    )
                    connection.execute(
                        'UPDATE puzzle_sessions SET expired_count = expired_count + 1 WHERE id = ?',
                        (session_id,),
                    )
                connection.execute(
                    "UPDATE puzzle_sessions SET status = 'expired', ended_at = ?, "
                    'current_puzzle_id = NULL, current_rating = NULL, current_fen = NULL, '
                    'current_orientation = NULL, current_moves_json = NULL WHERE id = ?',
                    (timestamp, session_id),
                )
            session = connection.execute(
                'SELECT * FROM puzzle_sessions WHERE id = ?', (session_id,)
            ).fetchone()
            return self._session_entry(session)

    def expire_due_sessions(self, now: datetime | None = None) -> None:
        timestamp = self._utc(now).isoformat()
        with self._connect() as connection:
            session_ids = [
                row['id'] for row in connection.execute(
                    "SELECT id FROM puzzle_sessions WHERE status = 'active' AND ends_at <= ?",
                    (timestamp,),
                )
            ]
        for session_id in session_ids:
            self.expire_session_if_due(session_id, now=now)

    @staticmethod
    def _session_entry(row: sqlite3.Row) -> dict:
        return {
            'id': row['id'],
            'started_at': row['started_at'],
            'ends_at': row['ends_at'],
            'ended_at': row['ended_at'],
            'duration_seconds': row['duration_seconds'],
            'status': row['status'],
            'completed_count': row['completed_count'],
            'successes': row['successes'],
            'failures': row['completed_count'] - row['successes'],
            'expired_count': row['expired_count'],
            'active_puzzle_id': row['current_puzzle_id'],
            'mode': row['mode'],
            'blindfold_depth': row['blindfold_depth'],
        }

    def get_session(self, session_id: str) -> dict | None:
        """Return a session without expiring it.

        Expiry is a write side effect (it records the in-progress puzzle as
        expired). Keeping this read-only lets the client ask the user whether
        they want to finish the current puzzle before that happens.
        """
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM puzzle_sessions WHERE id = ?', (session_id,)
            ).fetchone()
        if row is None:
            return None
        session = self._session_entry(row)
        remaining = datetime.fromisoformat(session['ends_at']) - self._utc()
        session['remaining_seconds'] = (
            max(0, math.ceil(remaining.total_seconds())) if session['status'] == 'active' else 0
        )
        return session

    def detach_session(self, session_id: str, now: datetime | None = None) -> dict | None:
        """End a session without recording its active puzzle as expired.

        Used when the player chooses to finish the in-progress puzzle after
        time runs out: the puzzle is then recorded on its own, outside the
        timed session.
        """
        timestamp = self._utc(now).isoformat()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            session = connection.execute(
                'SELECT * FROM puzzle_sessions WHERE id = ?', (session_id,)
            ).fetchone()
            if session is None:
                return None
            if session['status'] == 'active':
                connection.execute(
                    "UPDATE puzzle_sessions SET status = 'completed', ended_at = ?, "
                    'current_puzzle_id = NULL, current_rating = NULL, current_fen = NULL, '
                    'current_orientation = NULL, current_moves_json = NULL WHERE id = ?',
                    (timestamp, session_id),
                )
            session = connection.execute(
                'SELECT * FROM puzzle_sessions WHERE id = ?', (session_id,)
            ).fetchone()
            return self._session_entry(session)

    def get_sessions(self, limit: int = 500) -> list[dict]:
        self.expire_due_sessions()
        limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM puzzle_sessions WHERE status != 'active' "
                'ORDER BY started_at DESC LIMIT ?', (limit,)
            ).fetchall()
        return [self._session_entry(row) for row in rows]

    @staticmethod
    def _entry(row: sqlite3.Row) -> dict:
        return {
            'id': row['id'],
            'completed_at': row['completed_at'],
            'puzzle_id': row['puzzle_id'],
            'rating': row['rating'],
            'success': row['outcome'] == 'success',
            'outcome': row['outcome'],
            'fen': row['fen'],
            'orientation': row['orientation'],
            'moves': json.loads(row['moves_json']),
            'session_id': row['session_id'],
        }

    def get_history(self, limit: int = 500) -> list[dict]:
        self.expire_due_sessions()
        limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM puzzle_attempts "
                "ORDER BY completed_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._entry(row) for row in rows]

    def get_stats(self) -> dict:
        self.expire_due_sessions()
        with self._connect() as connection:
            summary = connection.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(success), 0) AS successes, "
                "COALESCE(AVG(rating), 0) AS average_rating "
                "FROM puzzle_attempts WHERE outcome != 'expired'"
            ).fetchone()
            outcomes = connection.execute(
                "SELECT success FROM puzzle_attempts WHERE outcome != 'expired' "
                "ORDER BY completed_at DESC, id DESC"
            ).fetchall()

        total = summary['total']
        successes = summary['successes']
        current_streak = 0
        for row in outcomes:
            if not row['success']:
                break
            current_streak += 1

        best_streak = 0
        streak = 0
        for row in reversed(outcomes):
            if row['success']:
                streak += 1
                best_streak = max(best_streak, streak)
            else:
                streak = 0

        return {
            'completed': total,
            'successes': successes,
            'success_rate': round(successes * 100 / total, 1) if total else 0,
            'average_rating': round(summary['average_rating']) if total else 0,
            'current_streak': current_streak,
            'best_streak': best_streak,
        }
