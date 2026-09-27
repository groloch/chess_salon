"""SQLite persistence and statistics for completed blindfold puzzles/sessions."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3
from pathlib import Path
import sys
import uuid


# ---------------------------------------------------------------------------
# Schema migrations
# ---------------------------------------------------------------------------
# The schema version is stored in SQLite's built-in ``user_version`` pragma.
# Every time a database is opened, any migration above the stored version is
# applied automatically, so shipping a new database format never requires a
# manual upgrade step. Each migration must be safe to run against a database
# that already contains the change, because databases created before versioning
# started at version 0 but may already include later columns.


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if column not in _column_names(connection, table):
        connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def _migration_1_initial_schema(connection: sqlite3.Connection) -> None:
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
            moves_json TEXT NOT NULL
        )
        """
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
            successes INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS puzzle_attempts_completed_at "
        "ON puzzle_attempts (completed_at DESC, id DESC)"
    )


def _migration_2_attempt_sessions(connection: sqlite3.Connection) -> None:
    _add_column_if_missing(connection, 'puzzle_attempts', 'session_id', 'TEXT')
    connection.execute(
        "CREATE INDEX IF NOT EXISTS puzzle_attempts_session "
        "ON puzzle_attempts (session_id, completed_at)"
    )


def _migration_3_attempt_outcomes(connection: sqlite3.Connection) -> None:
    if 'outcome' not in _column_names(connection, 'puzzle_attempts'):
        connection.execute(
            "ALTER TABLE puzzle_attempts ADD COLUMN outcome "
            "TEXT NOT NULL DEFAULT 'failure'"
        )
        connection.execute(
            "UPDATE puzzle_attempts SET outcome = CASE WHEN success = 1 "
            "THEN 'success' ELSE 'failure' END"
        )


def _migration_4_attempt_duration(connection: sqlite3.Connection) -> None:
    _add_column_if_missing(connection, 'puzzle_attempts', 'duration_ms', 'INTEGER')


def _migration_5_session_state(connection: sqlite3.Connection) -> None:
    for name, definition in (
        ('expired_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('mode', "TEXT NOT NULL DEFAULT 'easiest'"),
        ('blindfold_depth', 'INTEGER NOT NULL DEFAULT 9'),
        ('current_puzzle_id', 'TEXT'),
        ('current_rating', 'INTEGER'),
        ('current_fen', 'TEXT'),
        ('current_orientation', 'TEXT'),
        ('current_moves_json', 'TEXT'),
    ):
        _add_column_if_missing(connection, 'puzzle_sessions', name, definition)


def _migration_6_session_profile(connection: sqlite3.Connection) -> None:
    for name, definition in (
        ('profile_json', 'TEXT'),
        ('current_mode', 'TEXT'),
        ('current_blindfold_depth', 'INTEGER'),
    ):
        _add_column_if_missing(connection, 'puzzle_sessions', name, definition)


def _migration_7_attempt_mix(connection: sqlite3.Connection) -> None:
    _add_column_if_missing(connection, 'puzzle_attempts', 'mode', 'TEXT')
    _add_column_if_missing(connection, 'puzzle_attempts', 'blindfold_depth', 'INTEGER')


MIGRATIONS = {
    1: _migration_1_initial_schema,
    2: _migration_2_attempt_sessions,
    3: _migration_3_attempt_outcomes,
    4: _migration_4_attempt_duration,
    5: _migration_5_session_state,
    6: _migration_6_session_profile,
    7: _migration_7_attempt_mix,
}

SCHEMA_VERSION = max(MIGRATIONS)


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
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Apply any pending schema migrations tracked in ``user_version``."""
        current_version = connection.execute('PRAGMA user_version').fetchone()[0]
        for version in sorted(MIGRATIONS):
            if version <= current_version:
                continue
            MIGRATIONS[version](connection)
            connection.execute(f'PRAGMA user_version = {int(version)}')

    @property
    def schema_version(self) -> int:
        with self._connect() as connection:
            return connection.execute('PRAGMA user_version').fetchone()[0]

    def counts(self) -> dict[str, int]:
        """Return the number of stored attempts and sessions."""
        with self._connect() as connection:
            attempts = connection.execute(
                'SELECT COUNT(*) FROM puzzle_attempts'
            ).fetchone()[0]
            sessions = connection.execute(
                'SELECT COUNT(*) FROM puzzle_sessions'
            ).fetchone()[0]
        return {'attempts': attempts, 'sessions': sessions}

    def clear(self) -> None:
        """Delete every stored attempt and session (development helper)."""
        with self._connect() as connection:
            connection.execute('DELETE FROM puzzle_attempts')
            connection.execute('DELETE FROM puzzle_sessions')
            has_sequence = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'sqlite_sequence'"
            ).fetchone()
            if has_sequence:
                connection.execute(
                    "DELETE FROM sqlite_sequence WHERE name = 'puzzle_attempts'"
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
        duration_ms: int | None = None,
        mode: str | None = None,
        blindfold_depth: int | None = None,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO puzzle_attempts
                (completed_at, puzzle_id, rating, success, fen, orientation, moves_json, session_id, outcome, duration_ms, mode, blindfold_depth)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                completed_at, puzzle_id, int(rating), int(outcome == 'success'), fen,
                orientation, json.dumps(moves), session_id, outcome, duration_ms,
                mode, blindfold_depth,
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
        duration_ms: int | None = None,
        mode: str | None = None,
        blindfold_depth: int | None = None,
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
                            mode=session['current_mode'],
                            blindfold_depth=session['current_blindfold_depth'],
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
                duration_ms=duration_ms,
                mode=mode,
                blindfold_depth=blindfold_depth,
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
        mix=None,
        mode: str = 'easiest',
        blindfold_depth: int = 9,
        started_at: datetime | None = None,
    ) -> dict:
        started = self._utc(started_at)
        ends = started + timedelta(minutes=duration_minutes)
        session_id = str(uuid.uuid4())
        if mix is not None:
            mode = mix.difficulties[0]
            blindfold_depth = mix.depth_min
            profile_json = json.dumps(mix.to_payload())
        else:
            profile_json = json.dumps({
                'difficulties': [mode],
                'depth_min': blindfold_depth,
                'depth_max': blindfold_depth,
            })
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO puzzle_sessions "
                "(id, started_at, ends_at, duration_seconds, status, mode, blindfold_depth, profile_json) "
                "VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
                (
                    session_id, started.isoformat(), ends.isoformat(), duration_minutes * 60,
                    mode, blindfold_depth, profile_json,
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
        mode: str | None = None,
        blindfold_depth: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        timestamp_value = self._utc(now)
        timestamp = timestamp_value.isoformat()
        self.expire_session_if_due(session_id, now=timestamp_value)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE puzzle_sessions SET current_puzzle_id = ?, current_rating = ?, "
                "current_fen = ?, current_orientation = ?, current_moves_json = ?, "
                "current_mode = ?, current_blindfold_depth = ? "
                "WHERE id = ? AND status = 'active' AND ends_at > ?",
                (
                    puzzle_id, rating, fen, orientation, json.dumps(moves),
                    mode, blindfold_depth, session_id, timestamp,
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
                        mode=session['current_mode'],
                        blindfold_depth=session['current_blindfold_depth'],
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
                        mode=session['current_mode'],
                        blindfold_depth=session['current_blindfold_depth'],
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
        profile = None
        if row['profile_json']:
            try:
                profile = json.loads(row['profile_json'])
            except (TypeError, ValueError):
                profile = None
        if profile is None:
            profile = {
                'difficulties': [row['mode']],
                'depth_min': row['blindfold_depth'],
                'depth_max': row['blindfold_depth'],
            }
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
            'profile': profile,
            'active_mode': row['current_mode'],
            'active_blindfold_depth': row['current_blindfold_depth'],
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

        Used when the player chooses to keep training untimed after time runs
        out: the in-progress puzzle and any following ones are recorded outside
        the timed session.
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
            'duration_ms': row['duration_ms'],
            'mode': row['mode'],
            'blindfold_depth': row['blindfold_depth'],
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

    @staticmethod
    def _rate(successes: int, total: int) -> float:
        return round(successes * 100 / total, 1) if total else 0

    @staticmethod
    def _performance_rating(results: list[tuple[int, bool]]) -> int:
        """Estimate the rating at which the observed score is expected.

        Uses a maximum-likelihood Elo fit over every attempt, so it reflects
        performance against the difficulty actually faced rather than the raw
        average of puzzle ratings. The estimate is bounded to the range of
        puzzles attempted (plus a margin) so all-solved histories stay sane.
        """
        if not results:
            return 0
        ratings = [rating for rating, _ in results]
        actual = sum(1 for _, success in results if success)
        low = float(max(400, min(ratings) - 400))
        high = float(min(3200, max(ratings) + 400))
        if low >= high:
            return round(sum(ratings) / len(ratings))
        for _ in range(60):
            mid = (low + high) / 2
            expected = sum(
                1 / (1 + 10 ** ((rating - mid) / 400))
                for rating in ratings
            )
            if expected > actual:
                high = mid
            else:
                low = mid
        return round((low + high) / 2)

    def get_stats(self) -> dict:
        self.expire_due_sessions()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT rating, success, orientation, completed_at, duration_ms "
                "FROM puzzle_attempts WHERE outcome != 'expired' "
                "ORDER BY completed_at DESC, id DESC"
            ).fetchall()
            expired = connection.execute(
                "SELECT COUNT(*) FROM puzzle_attempts WHERE outcome = 'expired'"
            ).fetchone()[0]
            best_session = connection.execute(
                "SELECT COALESCE(MAX(successes), 0) FROM puzzle_sessions "
                "WHERE status != 'active'"
            ).fetchone()[0]

        total = len(rows)
        successes = sum(1 for row in rows if row['success'])
        ratings = [row['rating'] for row in rows]

        current_streak = 0
        for row in rows:
            if not row['success']:
                break
            current_streak += 1

        best_streak = 0
        streak = 0
        for row in reversed(rows):
            if row['success']:
                streak += 1
                best_streak = max(best_streak, streak)
            else:
                streak = 0

        recent = rows[:20]
        previous = rows[20:40]
        recent_rate = self._rate(sum(1 for r in recent if r['success']), len(recent))
        previous_rate = self._rate(sum(1 for r in previous if r['success']), len(previous))

        solved_ratings = [row['rating'] for row in rows if row['success']]
        durations = [row['duration_ms'] for row in rows if row['duration_ms'] is not None]

        white = [row for row in rows if row['orientation'] == 'white']
        black = [row for row in rows if row['orientation'] == 'black']
        white_successes = sum(1 for row in white if row['success'])
        black_successes = sum(1 for row in black if row['success'])

        completed_days = [
            self._utc(datetime.fromisoformat(row['completed_at'])).date()
            for row in rows
        ]
        day_set = set(completed_days)
        today = self._utc().date()
        cursor = today if today in day_set else today - timedelta(days=1)
        current_day_streak = 0
        while cursor in day_set:
            current_day_streak += 1
            cursor -= timedelta(days=1)
        week_start = today - timedelta(days=6)

        return {
            'completed': total,
            'successes': successes,
            'failures': total - successes,
            'success_rate': self._rate(successes, total),
            'average_rating': round(sum(ratings) / total) if total else 0,
            'current_streak': current_streak,
            'best_streak': best_streak,
            'recent_success_rate': recent_rate,
            'recent_trend': round(recent_rate - previous_rate, 1) if previous else None,
            'performance_rating': self._performance_rating(
                [(row['rating'], bool(row['success'])) for row in rows]
            ),
            'highest_rated_solved': max(solved_ratings) if solved_ratings else 0,
            'hardest_rating': max(ratings) if ratings else 0,
            'average_solved_rating': (
                round(sum(solved_ratings) / len(solved_ratings)) if solved_ratings else 0
            ),
            'white_attempts': len(white),
            'white_successes': white_successes,
            'white_success_rate': self._rate(white_successes, len(white)),
            'black_attempts': len(black),
            'black_successes': black_successes,
            'black_success_rate': self._rate(black_successes, len(black)),
            'puzzles_last_7_days': sum(1 for day in completed_days if day >= week_start),
            'active_days': len(day_set),
            'current_day_streak': current_day_streak,
            'best_session_solved': int(best_session),
            'average_solve_time_ms': (
                round(sum(durations) / len(durations)) if durations else 0
            ),
            'fastest_solve_ms': min(durations) if durations else 0,
            'expired_count': int(expired),
        }


# ---------------------------------------------------------------------------
# Development utility
# ---------------------------------------------------------------------------

def default_database_path() -> Path:
    """Return the instance database path used by the Flask app."""
    return Path(__file__).resolve().parents[1] / 'instance' / 'puzzle_history.sqlite3'


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m chess_trainer.puzzle_history',
        description='Manage the Chess Trainer puzzle history database.',
    )
    parser.add_argument(
        '--database', type=Path, default=default_database_path(),
        help='Path to the SQLite database (default: %(default)s).',
    )
    parser.add_argument(
        '--clear', action='store_true',
        help='Delete all stored attempts and sessions but keep the schema.',
    )
    parser.add_argument(
        '--reset', action='store_true',
        help='Delete the database file and recreate an empty, migrated schema.',
    )
    parser.add_argument(
        '--status', action='store_true',
        help='Print the schema version and row counts (default action).',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    path = Path(args.database)

    if args.reset:
        if path.exists():
            path.unlink()
        store = PuzzleHistoryStore(path)
        print(f'Recreated empty database at {path}')
        print(f'Schema version: {store.schema_version}')
        return 0

    if args.clear:
        store = PuzzleHistoryStore(path)
        store.clear()
        print(f'Cleared all puzzle attempts and sessions from {path}')
        return 0

    store = PuzzleHistoryStore(path)
    counts = store.counts()
    print(f'Database: {path}')
    print(f'Schema version: {store.schema_version} (latest is {SCHEMA_VERSION})')
    print(f'Attempts: {counts["attempts"]}')
    print(f'Sessions: {counts["sessions"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
