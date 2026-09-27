import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from chess_trainer.app import ChessApp  # noqa: E402
from chess_trainer.boards.puzzle_board import (  # noqa: E402
    BlindfoldPuzzleChessBoard,
    BlindfoldPuzzleConfig,
)
from chess_trainer.puzzle_history import PuzzleHistoryStore  # noqa: E402


class FakePuzzleClient:
    def __init__(self, puzzles):
        self._puzzles = iter(puzzles)
        self.puzzles = self

    def get_next(self, difficulty):
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
        self.assertEqual(reopened.get_stats(), {
            'completed': 3,
            'successes': 2,
            'success_rate': 66.7,
            'average_rating': 1400,
            'current_streak': 1,
            'best_streak': 1,
        })

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

    def test_streak_stats_and_empty_history(self):
        self.assertEqual(self.store.get_stats(), {
            'completed': 0,
            'successes': 0,
            'success_rate': 0,
            'average_rating': 0,
            'current_streak': 0,
            'best_streak': 0,
        })
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
        self.app_wrapper = ChessApp()
        self.app_wrapper.puzzle_history = PuzzleHistoryStore(
            Path(self.tempdir.name) / 'history.sqlite3'
        )
        self.client = self.app_wrapper.app.test_client()

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
        board = self.app_wrapper._boards['puzzles']
        self.assertEqual(board.kwargs['config'], BlindfoldPuzzleConfig('harder', 12))
        self.assertIs(board.kwargs['history_store'], self.app_wrapper.puzzle_history)


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


if __name__ == '__main__':
    unittest.main()
