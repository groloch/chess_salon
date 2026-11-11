import sys
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from chess_trainer.app import ChessApp  # noqa: E402
from chess_trainer.boards.puzzle_board import (  # noqa: E402
    BlindfoldPuzzleChessBoard,
    BlindfoldPuzzleConfig,
)
from chess_trainer.boards.puzzle_mix import PuzzleMix  # noqa: E402
from chess_trainer.puzzle_history import (  # noqa: E402
    SCHEMA_VERSION,
    PuzzleHistoryStore,
    main as puzzle_history_main,
)


class FakePuzzleClient:
    def __init__(self, puzzles):
        self._puzzles = iter(puzzles)
        self.puzzles = self
        self.requested = []

    def get_next(self, difficulty):
        self.requested.append(difficulty)
        return next(self._puzzles)


class PuzzleHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / 'history.sqlite3'
        self.store = PuzzleHistoryStore(self.database)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_persists_entries_and_calculates_stats(self):
        first_time = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
        self.store.record_attempt(
            puzzle_id='success-one', rating=1200, success=True,
            fen='fen-one', orientation='black', moves=[{'san': 'e4'}],
            completed_at=first_time,
        )
        self.store.record_attempt(
            puzzle_id='failure', rating=1400, success=False,
            fen='fen-two', orientation='white', moves=[],
            completed_at=datetime(2025, 1, 2, 12, tzinfo=timezone.utc),
        )
        self.store.record_attempt(
            puzzle_id='success-two', rating=1600, success=True,
            fen='fen-three', orientation='black', moves=[],
            completed_at=datetime(2025, 1, 3, 12, tzinfo=timezone.utc),
        )

        reopened = PuzzleHistoryStore(self.database)
        self.assertEqual([entry['puzzle_id'] for entry in reopened.get_history()], [
            'success-two', 'failure', 'success-one',
        ])
        self.assertEqual(reopened.get_history()[0]['orientation'], 'black')
        stats = reopened.get_stats()
        self.assertEqual(stats['completed'], 3)
        self.assertEqual(stats['successes'], 2)
        self.assertEqual(stats['failures'], 1)
        self.assertEqual(stats['success_rate'], 66.7)
        self.assertEqual(stats['average_rating'], 1400)
        self.assertEqual(stats['current_streak'], 1)
        self.assertEqual(stats['best_streak'], 1)
        self.assertEqual(stats['recent_success_rate'], 66.7)
        self.assertIsNone(stats['recent_trend'])
        self.assertEqual(stats['highest_rated_solved'], 1600)
        self.assertEqual(stats['average_solved_rating'], 1400)
        self.assertEqual(stats['white_attempts'], 1)
        self.assertEqual(stats['white_successes'], 0)
        self.assertEqual(stats['black_attempts'], 2)
        self.assertEqual(stats['black_successes'], 2)
        self.assertEqual(stats['black_success_rate'], 100.0)
        self.assertEqual(stats['average_solve_time_ms'], 0)

    def test_migrates_legacy_database_to_current_schema(self):
        legacy_path = Path(self.tempdir.name) / 'legacy.sqlite3'
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE puzzle_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                completed_at TEXT NOT NULL,
                puzzle_id TEXT NOT NULL,
                rating INTEGER NOT NULL,
                success INTEGER NOT NULL,
                fen TEXT NOT NULL,
                orientation TEXT NOT NULL,
                moves_json TEXT NOT NULL
            );
            CREATE TABLE puzzle_sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                ended_at TEXT,
                duration_seconds INTEGER NOT NULL,
                status TEXT NOT NULL,
                completed_count INTEGER NOT NULL DEFAULT 0,
                successes INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO puzzle_attempts
                (completed_at, puzzle_id, rating, success, fen, orientation, moves_json)
            VALUES
                ('2025-01-01T12:00:00+00:00', 'legacy', 1300, 1, 'fen', 'white', '[]');
            """
        )
        connection.commit()
        connection.close()

        store = PuzzleHistoryStore(legacy_path)
        self.assertEqual(store.schema_version, SCHEMA_VERSION)
        attempt = store.get_history()[0]
        self.assertEqual(attempt['outcome'], 'success')
        self.assertIsNone(attempt['duration_ms'])
        # Columns added by later migrations are immediately usable.
        session = store.start_session(5)
        self.assertEqual(session['mode'], 'easiest')
        self.assertEqual(session['blindfold_depth'], 9)

    def test_migration_is_idempotent(self):
        reopened = PuzzleHistoryStore(self.database)
        self.assertEqual(reopened.schema_version, SCHEMA_VERSION)
        reopened = PuzzleHistoryStore(self.database)
        self.assertEqual(reopened.schema_version, SCHEMA_VERSION)

    def test_clear_removes_all_records_but_keeps_schema(self):
        self.store.record_attempt(
            puzzle_id='one', rating=1200, success=True,
            fen='fen', orientation='white', moves=[],
        )
        session = self.store.start_session(10)
        self.store.set_session_puzzle(
            session['id'], puzzle_id='two', rating=1300,
            fen='fen', orientation='black', moves=[],
        )
        self.assertEqual(self.store.counts(), {'attempts': 1, 'sessions': 1})

        self.store.clear()

        self.assertEqual(self.store.get_history(), [])
        self.assertEqual(self.store.get_sessions(), [])
        self.assertEqual(self.store.counts(), {'attempts': 0, 'sessions': 0})
        self.assertEqual(self.store.get_stats()['completed'], 0)
        self.assertEqual(self.store.schema_version, SCHEMA_VERSION)
        # The store still records new attempts after being cleared.
        self.store.record_attempt(
            puzzle_id='three', rating=1400, success=False,
            fen='fen', orientation='black', moves=[],
        )
        self.assertEqual(len(self.store.get_history()), 1)

    def test_daily_progress_is_scoped_and_uses_all_attempts(self):
        base = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
        for index in range(502):
            self.store.record_attempt(
                puzzle_id=f'progress-{index}', rating=1500, success=index % 2 == 0,
                fen='fen', orientation='white', moves=[], user_id='owner',
                completed_at=base, duration_ms=10000 if index % 2 == 0 else 30000,
            )
        self.store.record_attempt(
            puzzle_id='next-day', rating=1600, success=True,
            fen='fen', orientation='white', moves=[], user_id='owner',
            completed_at=base + timedelta(days=1),
        )
        self.store.record_attempt(
            puzzle_id='other-user', rating=2500, success=True,
            fen='fen', orientation='white', moves=[], user_id='other',
            completed_at=base,
        )
        progress = self.store.get_progress('owner')
        self.assertEqual(len(self.store.get_history(user_id='owner')), 500)
        self.assertEqual(progress[0], {
            'date': '2025-01-01', 'performance_rating': 1500,
            'average_rating': 1500, 'average_solve_time_ms': 20000,
        })
        self.assertEqual(progress[1]['date'], '2025-01-02')
        self.assertIsNone(progress[1]['average_solve_time_ms'])
        self.assertEqual(self.store.get_progress('unknown'), [])

    def test_performance_and_solve_time_stats(self):
        base = datetime.now(timezone.utc)
        self.store.record_attempt(
            puzzle_id='rated-even', rating=1500, success=True,
            fen='fen', orientation='white', moves=[],
            completed_at=base, duration_ms=10000,
        )
        self.store.record_attempt(
            puzzle_id='rated-even-miss', rating=1500, success=False,
            fen='fen', orientation='white', moves=[],
            completed_at=base + timedelta(seconds=1), duration_ms=30000,
        )
        stats = self.store.get_stats()
        # One win and one loss at the same rating places performance at that rating.
        self.assertEqual(stats['performance_rating'], 1500)
        self.assertEqual(stats['average_solve_time_ms'], 20000)
        self.assertEqual(stats['fastest_solve_ms'], 10000)
        self.assertEqual(stats['puzzles_last_7_days'], 2)
        self.assertEqual(stats['active_days'], 1)
        self.assertEqual(stats['current_day_streak'], 1)

    def test_manually_ending_session_records_open_puzzle_as_expired(self):
        started = datetime.now(timezone.utc)
        session = self.store.start_session(30, started_at=started)
        self.store.set_session_puzzle(
            session['id'], puzzle_id='unfinished', rating=1100,
            fen='position', orientation='white', moves=[], now=started,
        )
        ended = self.store.end_session(session['id'], now=started + timedelta(minutes=3))
        self.assertEqual(ended['status'], 'expired')
        self.assertEqual(ended['expired_count'], 1)
        self.assertIsNone(ended['active_puzzle_id'])
        self.assertEqual(self.store.get_history()[0]['outcome'], 'expired')
        self.assertEqual(self.store.get_history()[0]['puzzle_id'], 'unfinished')

    def test_timed_session_expires_open_puzzle_as_expired_not_failure(self):
        started = datetime.now(timezone.utc)
        session = self.store.start_session(1, started_at=started)
        self.assertTrue(self.store.set_session_puzzle(
            session['id'],
            puzzle_id='left-open', rating=1500, fen='position',
            orientation='black', moves=[{'san': 'e5'}], now=started,
        ))

        # Expiry is evaluated against the supplied deadline deterministically.
        expired = self.store.expire_session_if_due(
            session['id'], now=started + timedelta(minutes=1),
        )
        self.assertEqual(expired['status'], 'expired')
        history = self.store.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['outcome'], 'expired')
        self.assertFalse(history[0]['success'])
        self.assertEqual(history[0]['puzzle_id'], 'left-open')
        self.assertEqual(expired['expired_count'], 1)
        self.assertIsNone(expired['active_puzzle_id'])
        self.assertEqual(self.store.get_history()[0]['outcome'], 'expired')
        stats = self.store.get_stats()
        self.assertEqual(stats['completed'], 0)

    def test_attempt_exactly_at_deadline_is_expired(self):
        started = datetime.now(timezone.utc)
        session = self.store.start_session(1, started_at=started)
        self.store.set_session_puzzle(
            session['id'], puzzle_id='deadline', rating=1200,
            fen='fen', orientation='white', moves=[], now=started,
        )
        attempt_id = self.store.record_attempt(
            puzzle_id='deadline', rating=1200, success=False,
            fen='fen', orientation='white', moves=[],
            completed_at=started + timedelta(minutes=1), session_id=session['id'],
        )
        self.assertIsNone(attempt_id)
        self.assertEqual(self.store.get_history()[0]['outcome'], 'expired')

    def test_completing_after_deadline_expires_current_puzzle_not_as_failure(self):
        started = datetime.now(timezone.utc)
        session = self.store.start_session(1, started_at=started)
        self.store.set_session_puzzle(
            session['id'], puzzle_id='active-at-deadline', rating=1200,
            fen='fen', orientation='white', moves=[], now=started,
        )
        result = self.store.record_attempt(
            puzzle_id='active-at-deadline', rating=1200, success=True,
            fen='fen', orientation='white', moves=[],
            completed_at=started + timedelta(minutes=1), session_id=session['id'],
        )
        self.assertIsNone(result)
        history = self.store.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['outcome'], 'expired')
        self.assertEqual(self.store.get_session(session['id'])['expired_count'], 1)

    def test_get_session_does_not_expire_in_progress_puzzle(self):
        started = datetime.now(timezone.utc) - timedelta(minutes=2)
        session = self.store.start_session(1, started_at=started)
        self.store.set_session_puzzle(
            session['id'], puzzle_id='still-open', rating=1200,
            fen='position', orientation='white', moves=[], now=started,
        )
        # Reading the session is side-effect free: the puzzle is not counted yet.
        read = self.store.get_session(session['id'])
        self.assertEqual(read['status'], 'active')
        self.assertEqual(read['remaining_seconds'], 0)
        with self.store._connect() as connection:
            self.assertEqual(
                connection.execute('SELECT COUNT(*) FROM puzzle_attempts').fetchone()[0], 0,
            )
        # Dashboard cleanup still expires abandoned sessions.
        self.store.expire_due_sessions()
        self.assertEqual(self.store.get_history()[0]['outcome'], 'expired')

    def test_detach_session_switches_to_untimed_training(self):
        started = datetime.now(timezone.utc)
        session = self.store.start_session(30, started_at=started)
        self.store.set_session_puzzle(
            session['id'], puzzle_id='carry-over', rating=1300,
            fen='position', orientation='white', moves=[], now=started,
        )
        detached = self.store.detach_session(session['id'], now=started + timedelta(minutes=5))
        self.assertEqual(detached['status'], 'completed')
        self.assertIsNone(detached['active_puzzle_id'])
        self.assertEqual(detached['expired_count'], 0)
        self.assertEqual(self.store.get_history(), [])

        # The board then records the finished puzzle without a session id.
        self.store.record_attempt(
            puzzle_id='carry-over', rating=1300, success=True,
            fen='position', orientation='white', moves=[],
            completed_at=started + timedelta(minutes=6),
        )
        history = self.store.get_history()
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]['success'])
        self.assertIsNone(history[0]['session_id'])

    def test_session_round_trips_profile(self):
        mix = PuzzleMix(('normal', 'harder'), 5, 10)
        session = self.store.start_session(30, mix=mix)
        self.assertEqual(session['profile'], mix.to_payload())
        self.assertEqual(session['mode'], 'normal')
        self.assertEqual(session['blindfold_depth'], 5)

    def test_legacy_session_gets_fixed_profile(self):
        session = self.store.start_session(10)
        self.assertEqual(session['profile'], {
            'difficulties': ['easiest'], 'depth_min': 9, 'depth_max': 9,
        })

    def test_attempt_records_sampled_mix(self):
        self.store.record_attempt(
            puzzle_id='mix', rating=1200, success=True, fen='fen',
            orientation='white', moves=[], mode='harder', blindfold_depth=12,
        )
        entry = self.store.get_history()[0]
        self.assertEqual(entry['mode'], 'harder')
        self.assertEqual(entry['blindfold_depth'], 12)

    def test_streak_stats_and_empty_history(self):
        empty = self.store.get_stats()
        self.assertEqual(empty['completed'], 0)
        self.assertEqual(empty['successes'], 0)
        self.assertEqual(empty['success_rate'], 0)
        self.assertEqual(empty['average_rating'], 0)
        self.assertEqual(empty['current_streak'], 0)
        self.assertEqual(empty['best_streak'], 0)
        self.assertEqual(empty['performance_rating'], 0)
        self.assertEqual(empty['average_solve_time_ms'], 0)
        self.assertEqual(empty['active_days'], 0)
        self.assertIsNone(empty['recent_trend'])
        for index, success in enumerate([True, False, True, True, True]):
            self.store.record_attempt(
                puzzle_id=str(index), rating=1000, success=success,
                fen='fen', orientation='white', moves=[],
                completed_at=datetime(2025, 1, 1, index, tzinfo=timezone.utc),
            )
        stats = self.store.get_stats()
        self.assertEqual(stats['current_streak'], 3)
        self.assertEqual(stats['best_streak'], 3)


class PuzzleStartApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.app_wrapper = ChessApp(
            env_path=str(root / '.env'),
            database_path=str(root / 'history.sqlite3'),
        )
        self.user = self.app_wrapper.user_store.save_lichess_account(
            lichess_id='tester-1', username='Tester', access_token='test-token',
        )
        self.client = self.app_wrapper.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.user['id']

    def tearDown(self):
        self.tempdir.cleanup()

    def test_rejects_invalid_settings(self):
        response = self.client.post('/api/puzzles/start', json={
            'mode': 'impossible', 'blindfold_depth': 9,
        })
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/puzzles/start', json={
            'mode': 'normal', 'blindfold_depth': 0,
        })
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/puzzles/start', json={
            'mode': 'normal', 'blindfold_depth': 9, 'timed': True,
            'duration_minutes': 181,
        })
        self.assertEqual(response.status_code, 400)

    def test_starts_timed_session_and_associates_initial_position(self):
        class FakeBoard:
            def __init__(self, **kwargs):
                self.current_puzzle_id = 'first'
                self.rating = 1400
                self.puzzle_start_fen = 'fen'
                self.orientation = 'black'
                self.puzzle_start_moves = []
                self.kwargs = kwargs

        with patch('chess_trainer.app.BlindfoldPuzzleChessBoard', FakeBoard):
            response = self.client.post('/api/puzzles/start', json={
                'mode': 'normal', 'blindfold_depth': 9,
                'timed': True, 'duration_minutes': 30,
            })
        self.assertEqual(response.status_code, 200)
        session = response.json['session']
        self.assertIn(session['id'], response.json['redirect_url'])
        saved = self.app_wrapper.puzzle_history.get_session(session['id'])
        self.assertEqual(saved['active_puzzle_id'], 'first')
        self.assertEqual(saved['duration_seconds'], 1800)

    def test_starts_a_puzzle_with_selected_configuration(self):
        class FakeBoard:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        with patch('chess_trainer.app.BlindfoldPuzzleChessBoard', FakeBoard):
            response = self.client.post('/api/puzzles/start', json={
                'mode': 'harder', 'blindfold_depth': 12,
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['redirect_url'], '/blind_puzzles/play')
        self.assertEqual(response.json['profile'], {
            'difficulties': ['harder'], 'depth_min': 12, 'depth_max': 12,
        })
        board = self.app_wrapper._boards[(self.user['id'], 'puzzles')]
        self.assertEqual(board.kwargs['mix'], PuzzleMix.fixed('harder', 12))
        self.assertIs(board.kwargs['history_store'], self.app_wrapper.puzzle_history)

    def test_detach_route_ends_session_and_untracks_board(self):
        class FakeBoard:
            def __init__(self, **kwargs):
                self.current_puzzle_id = 'first'
                self.rating = 1400
                self.puzzle_start_fen = 'fen'
                self.orientation = 'black'
                self.puzzle_start_moves = []
                self.session_id = None
                self.kwargs = kwargs

        with patch('chess_trainer.app.BlindfoldPuzzleChessBoard', FakeBoard):
            start = self.client.post('/api/puzzles/start', json={
                'mode': 'normal', 'blindfold_depth': 9,
                'timed': True, 'duration_minutes': 30,
            })
        session_id = start.json['session']['id']
        self.assertEqual(self.app_wrapper._boards[(self.user['id'], 'puzzles')].session_id, session_id)

        response = self.client.post(f'/api/puzzles/session/{session_id}/detach')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['session']['status'], 'completed')
        self.assertIsNone(self.app_wrapper._boards[(self.user['id'], 'puzzles')].session_id)
        self.assertEqual(self.app_wrapper.puzzle_history.get_history(), [])


class PuzzleBoardHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = PuzzleHistoryStore(Path(self.tempdir.name) / 'history.sqlite3')
        self.puzzle = {
            'game': {'pgn': 'e4 e5 Nf3 Nc6'},
            'puzzle': {
                'id': 'mock-puzzle',
                'rating': 1350,
                'solution': ['b1c3', 'g8f6', 'd2d4', 'd7d6'],
            },
        }
        next_puzzle = {
            'game': {'pgn': 'd4 d5 c4 e6'},
            'puzzle': {
                'id': 'next-puzzle', 'rating': 1500,
                'solution': ['b1c3', 'g8f6', 'd2d4', 'd7d6'],
            },
        }
        client = FakePuzzleClient([self.puzzle, next_puzzle])
        self.board = BlindfoldPuzzleChessBoard(
            client=client, token='test-token', history_store=self.store
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_failure_recorded_once_before_next_puzzle_load(self):
        puzzle_start_fen = self.board.puzzle_start_fen
        with patch.object(self.board, 'notify_puzzle_completed', return_value=True):
            result = self.board.push_move('a2a3')
        self.assertTrue(result['completed'])
        entries = self.store.get_history()
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]['success'])
        self.assertEqual(entries[0]['puzzle_id'], 'mock-puzzle')
        self.assertEqual(entries[0]['fen'], puzzle_start_fen)
        self.assertEqual(self.board.current_puzzle_id, 'next-puzzle')

    def test_board_samples_from_configured_mix(self):
        mix = PuzzleMix(('harder', 'hardest'), 4, 4)
        client = FakePuzzleClient([self.puzzle])
        board = BlindfoldPuzzleChessBoard(client=client, token='test-token', mix=mix)
        self.assertIn(client.requested[0], mix.difficulties)
        self.assertIn(board.current_mode, mix.difficulties)
        self.assertEqual(board.current_blindfold_depth, 4)

    def test_success_recorded_after_solution(self):
        # The first correct move advances to the opponent response; the second
        # correct move completes this two-ply puzzle line.
        with patch.object(self.board, 'notify_puzzle_completed', return_value=True):
            self.board.push_move('b1c3')
            result = self.board.push_move('d2d4')
        self.assertTrue(result['win'])
        entries = self.store.get_history()
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]['success'])
        self.assertIsNotNone(entries[0]['duration_ms'])
        self.assertGreaterEqual(entries[0]['duration_ms'], 0)


class PuzzleMixTests(unittest.TestCase):
    def test_fixed_mix_is_deterministic(self):
        mix = PuzzleMix.fixed('normal', 9)
        self.assertEqual(mix.sample(), ('normal', 9))

    def test_from_payload_accepts_new_and_legacy_shapes(self):
        self.assertEqual(
            PuzzleMix.from_payload({
                'difficulties': ['normal', 'harder'], 'depth_min': 5, 'depth_max': 10,
            }),
            PuzzleMix(('normal', 'harder'), 5, 10),
        )
        self.assertEqual(
            PuzzleMix.from_payload({'mode': 'easiest', 'blindfold_depth': 9}),
            PuzzleMix.fixed('easiest', 9),
        )

    def test_from_payload_normalizes_and_validates(self):
        # Duplicates are removed and reversed depths are swapped.
        mix = PuzzleMix.from_payload({
            'difficulties': ['normal', 'normal'], 'depth_min': 12, 'depth_max': 4,
        })
        self.assertEqual(mix, PuzzleMix(('normal',), 4, 12))
        for invalid in (
            {'difficulties': []},
            {'difficulties': ['impossible']},
            {'difficulties': ['normal'], 'depth_min': 0, 'depth_max': 5},
            {'mode': 'normal', 'blindfold_depth': 41},
        ):
            with self.assertRaises(ValueError):
                PuzzleMix.from_payload(invalid)

    def test_sample_stays_within_support(self):
        import random as random_module

        mix = PuzzleMix(('normal', 'harder'), 5, 8)
        rng = random_module.Random(1234)
        for _ in range(50):
            mode, depth = mix.sample(rng)
            self.assertIn(mode, mix.difficulties)
            self.assertGreaterEqual(depth, 5)
            self.assertLessEqual(depth, 8)

    def test_payload_round_trip(self):
        mix = PuzzleMix(('easiest', 'hardest'), 3, 15)
        self.assertEqual(PuzzleMix.from_payload(mix.to_payload()), mix)


class PuzzleHistoryCliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / 'history.sqlite3'

    def tearDown(self):
        self.tempdir.cleanup()

    def _record(self, puzzle_id):
        PuzzleHistoryStore(self.database).record_attempt(
            puzzle_id=puzzle_id, rating=1200, success=True,
            fen='fen', orientation='white', moves=[],
        )

    def test_clear_keeps_schema_and_removes_rows(self):
        self._record('one')
        with redirect_stdout(io.StringIO()):
            result = puzzle_history_main(['--database', str(self.database), '--clear'])
        self.assertEqual(result, 0)
        store = PuzzleHistoryStore(self.database)
        self.assertEqual(store.counts()['attempts'], 0)
        self.assertEqual(store.schema_version, SCHEMA_VERSION)

    def test_reset_recreates_an_empty_database(self):
        self._record('one')
        with redirect_stdout(io.StringIO()):
            result = puzzle_history_main(['--database', str(self.database), '--reset'])
        self.assertEqual(result, 0)
        store = PuzzleHistoryStore(self.database)
        self.assertEqual(store.counts(), {'attempts': 0, 'sessions': 0})
        self.assertEqual(store.schema_version, SCHEMA_VERSION)


class PuzzleDashboardPageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.app_wrapper = ChessApp(
            env_path=str(root / '.env'),
            database_path=str(root / 'history.sqlite3'),
        )
        self.user = self.app_wrapper.user_store.save_lichess_account(
            lichess_id='dash-1', username='Dash', access_token='test-token',
        )
        self.other = self.app_wrapper.user_store.save_lichess_account(
            lichess_id='dash-2', username='Other', access_token='other-token',
        )
        self.client = self.app_wrapper.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.user['id']

    def tearDown(self):
        self.tempdir.cleanup()

    def _client_for(self, user):
        client = self.app_wrapper.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = user['id']
        return client

    def test_anonymous_gets_sign_in_wall_on_both_routes(self):
        anonymous = self.app_wrapper.app.test_client()
        for path in ('/blind_puzzles', '/puzzle_history'):
            response = anonymous.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Sign in with Lichess', response.data)
            self.assertNotIn(b'id="progress-data"', response.data)
            self.assertNotIn(b'puzzle-card', response.data)

    def test_dashboard_renders_history_server_side_grouped_by_utc_day(self):
        store = self.app_wrapper.puzzle_history
        store.record_attempt(
            puzzle_id='card-one', rating=1500, success=True, fen='fen-one',
            orientation='white', moves=[], duration_ms=95000,
            completed_at=datetime(2025, 3, 1, 23, 30, tzinfo=timezone.utc),
            user_id=self.user['id'],
        )
        store.record_attempt(
            puzzle_id='card-two', rating=1600, success=False, fen='fen-two',
            orientation='black', moves=[],
            completed_at=datetime(2025, 3, 2, 0, 30, tzinfo=timezone.utc),
            user_id=self.user['id'],
        )
        store.record_attempt(
            puzzle_id='other-user-card', rating=2500, success=True, fen='fen-x',
            orientation='white', moves=[],
            completed_at=datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc),
            user_id=self.other['id'],
        )
        for path in ('/blind_puzzles', '/puzzle_history'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('Blindfold puzzles', html)
            # Server-rendered cards and UTC day headings, no JS-built markup.
            self.assertIn('puzzle-card', html)
            self.assertIn('card-one', html)
            self.assertIn('datetime="2025-03-01T00:00:00+00:00"', html)
            self.assertIn('datetime="2025-03-02T00:00:00+00:00"', html)
            # Chart data embedded via tojson.
            self.assertIn('id="progress-data"', html)
            self.assertIn('performance_rating', html)
            # User isolation: the other user's puzzle never leaks.
            self.assertNotIn('other-user-card', html)

    def test_dashboard_renders_timed_session_frame(self):
        store = self.app_wrapper.puzzle_history
        started = datetime(2025, 3, 1, 10, 0, tzinfo=timezone.utc)
        session = store.start_session(30, started_at=started, user_id=self.user['id'])
        self.assertTrue(store.set_session_puzzle(
            session['id'], puzzle_id='session-puzzle', rating=1400,
            fen='fen-s', orientation='white', moves=[], now=started,
        ))
        store.record_attempt(
            puzzle_id='session-puzzle', rating=1400, success=True, fen='fen-s',
            orientation='white', moves=[], session_id=session['id'],
            completed_at=started + timedelta(minutes=10), user_id=self.user['id'],
        )
        store.end_session(session['id'], now=started + timedelta(minutes=20))
        html = self.client.get('/blind_puzzles').get_data(as_text=True)
        self.assertIn('timed-history-frame', html)
        self.assertIn('30 minute session', html)
        self.assertIn('1 completed · 1 solved · 0 missed · 0 timed out', html)
        self.assertIn('session-puzzle', html)
        self.assertIn('Easiest · depth 9', html)
        self.assertNotIn('Fixed:', html)
        self.assertNotIn('Random draw', html)

    def test_dashboard_escapes_user_data(self):
        store = self.app_wrapper.puzzle_history
        store.record_attempt(
            puzzle_id='<script>alert(1)</script>', rating=1200, success=True,
            fen='fen"><b>x</b>', orientation='white', moves=[],
            completed_at=datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc),
            user_id=self.user['id'],
        )
        html = self.client.get('/blind_puzzles').get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>', html)
        # The puzzle id only appears URL-quoted inside the Lichess link.
        self.assertIn('training/%3Cscript%3Ealert%281%29%3C%2Fscript%3E', html)
        self.assertNotIn('fen"><b>x</b>', html)
        self.assertIn('data-fen="fen&#34;&gt;&lt;b&gt;x&lt;/b&gt;"', html)

    def test_empty_dashboard_has_status_and_no_chart_section(self):
        html = self._client_for(self.other).get('/blind_puzzles').get_data(as_text=True)
        self.assertIn('No completed puzzles yet.', html)
        self.assertIn('id="history-status"', html)
        self.assertNotIn('id="progress-data"', html)
        self.assertNotIn('history-day', html)

    def test_sessions_without_attempts_get_their_own_empty_message(self):
        store = self.app_wrapper.puzzle_history
        started = datetime(2025, 3, 1, 10, 0, tzinfo=timezone.utc)
        session = store.start_session(30, started_at=started, user_id=self.other['id'])
        store.end_session(session['id'], now=started + timedelta(minutes=5))
        html = self._client_for(self.other).get('/blind_puzzles').get_data(as_text=True)
        self.assertIn('Timed sessions will appear here when they finish.', html)


class PuzzlePresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = PuzzleHistoryStore(Path(self.tempdir.name) / 'history.sqlite3')

    def tearDown(self):
        self.tempdir.cleanup()

    def test_presets_are_scoped_per_user(self):
        first = self.store.save_preset(
            user_id='u1', name='One',
            profile={'difficulties': ['normal'], 'depth_min': 9, 'depth_max': 9},
        )
        self.store.save_preset(
            user_id='u2', name='Two',
            profile={'difficulties': ['easiest'], 'depth_min': 5, 'depth_max': 6},
        )
        presets = self.store.list_presets('u1')
        self.assertEqual([p['name'] for p in presets], ['One'])
        self.assertEqual(presets[0]['profile']['difficulties'], ['normal'])
        self.assertFalse(presets[0]['is_default'])
        # Deletion is ownership-checked.
        self.assertFalse(self.store.delete_preset(first['id'], 'u2'))
        self.assertTrue(self.store.delete_preset(first['id'], 'u1'))
        self.assertEqual(self.store.list_presets('u1'), [])

    def test_ensure_default_preset_ignores_unknown_user(self):
        self.store.ensure_default_preset('no-such-user')
        self.assertEqual(self.store.list_presets('no-such-user'), [])


class PuzzlePresetApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.app_wrapper = ChessApp(
            env_path=str(root / '.env'),
            database_path=str(root / 'history.sqlite3'),
        )
        self.user = self.app_wrapper.user_store.save_lichess_account(
            lichess_id='preset-1', username='PresetOne', access_token='test-token',
        )
        self.other = self.app_wrapper.user_store.save_lichess_account(
            lichess_id='preset-2', username='PresetTwo', access_token='other-token',
        )
        self.client = self.app_wrapper.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.user['id']

    def tearDown(self):
        self.tempdir.cleanup()

    def _client_for(self, user):
        client = self.app_wrapper.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = user['id']
        return client

    def test_anonymous_presets_require_sign_in(self):
        anonymous = self.app_wrapper.app.test_client()
        self.assertEqual(anonymous.get('/api/puzzles/presets').status_code, 401)
        self.assertEqual(anonymous.post('/api/puzzles/presets', json={'name': 'x'}).status_code, 401)
        self.assertEqual(anonymous.delete('/api/puzzles/presets/whatever').status_code, 401)

    def test_default_preset_is_seeded_once_and_deletable(self):
        payload = self.client.get('/api/puzzles/presets').get_json()
        self.assertEqual(len(payload['presets']), 1)
        default = payload['presets'][0]
        self.assertEqual(default['name'], 'Build')
        self.assertTrue(default['is_default'])
        self.assertEqual(default['profile'], {'difficulties': ['easier'], 'depth_min': 9, 'depth_max': 11})
        # Deleting the default sticks: it is not re-seeded on the next list.
        response = self.client.delete(f"/api/puzzles/presets/{default['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/puzzles/presets').get_json()['presets'], [])

    def test_create_list_and_delete_preset(self):
        response = self.client.post('/api/puzzles/presets', json={
            'name': 'My mix',
            'difficulties': ['normal', 'harder'],
            'depth_min': 5,
            'depth_max': 10,
        })
        self.assertEqual(response.status_code, 201)
        preset = response.get_json()['preset']
        self.assertEqual(preset['name'], 'My mix')
        self.assertEqual(
            preset['profile'],
            {'difficulties': ['normal', 'harder'], 'depth_min': 5, 'depth_max': 10},
        )
        names = [p['name'] for p in self.client.get('/api/puzzles/presets').get_json()['presets']]
        self.assertEqual(names, ['Build', 'My mix'])

        # The other user cannot see or delete it.
        other_client = self._client_for(self.other)
        other_names = [p['name'] for p in other_client.get('/api/puzzles/presets').get_json()['presets']]
        self.assertEqual(other_names, ['Build'])
        self.assertEqual(other_client.delete(f"/api/puzzles/presets/{preset['id']}").status_code, 404)

        self.assertEqual(self.client.delete(f"/api/puzzles/presets/{preset['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/puzzles/presets/{preset['id']}").status_code, 404)

    def test_create_preset_validates_name_and_profile(self):
        for payload in (
            {'difficulties': ['normal'], 'depth_min': 9, 'depth_max': 9},  # no name
            {'name': 'x' * 41, 'difficulties': ['normal'], 'depth_min': 9, 'depth_max': 9},
            {'name': 'bad', 'difficulties': ['impossible'], 'depth_min': 9, 'depth_max': 9},
            {'name': 'bad', 'difficulties': ['normal'], 'depth_min': 0, 'depth_max': 5},
        ):
            response = self.client.post('/api/puzzles/presets', json=payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn('error', response.get_json())
        # Legacy fixed profile payloads are still accepted.
        response = self.client.post('/api/puzzles/presets', json={
            'name': 'Legacy', 'mode': 'harder', 'blindfold_depth': 12,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['preset']['profile'], {
            'difficulties': ['harder'], 'depth_min': 12, 'depth_max': 12,
        })


if __name__ == '__main__':
    unittest.main()
