import base64
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from chess_trainer import auth  # noqa: E402
from chess_trainer.app import ChessApp  # noqa: E402
from chess_trainer.puzzle_history import SCHEMA_VERSION, PuzzleHistoryStore  # noqa: E402
from chess_trainer.user_store import UserStore  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class AuthConfigTests(unittest.TestCase):
    def test_generates_and_persists_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / '.env'
            config = auth.load_auth_config(str(env))
            self.assertTrue(config.secret_key)
            self.assertTrue(config.token_encryption_key)
            again = auth.load_auth_config(str(env))
            self.assertEqual(again.secret_key, config.secret_key)
            self.assertEqual(again.token_encryption_key, config.token_encryption_key)
            self.assertIsNone(again.lichess_client_id)
            self.assertEqual(again.lichess_redirect_uri, auth.DEFAULT_REDIRECT_URI)
            self.assertEqual(again.lichess_scopes, 'puzzle:read puzzle:write')


class PkceTests(unittest.TestCase):
    def test_verifier_challenge_pair(self):
        verifier, challenge = auth.generate_pkce_pair()
        self.assertTrue(43 <= len(verifier) <= 128)
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode('ascii')).digest()
        ).rstrip(b'=').decode('ascii')
        self.assertEqual(challenge, expected)

    def test_authorize_url_contains_pkce_and_state(self):
        url = auth.build_authorize_url(
            'cid', 'http://localhost:23777/auth/callback',
            'puzzle:read puzzle:write', 'state123', 'challenge456',
        )
        parsed = urlparse(url)
        self.assertEqual(parsed.netloc, 'lichess.org')
        self.assertEqual(parsed.path, '/oauth')
        qs = parse_qs(parsed.query)
        self.assertEqual(qs['response_type'], ['code'])
        self.assertEqual(qs['client_id'], ['cid'])
        self.assertEqual(qs['redirect_uri'], ['http://localhost:23777/auth/callback'])
        self.assertEqual(qs['code_challenge'], ['challenge456'])
        self.assertEqual(qs['code_challenge_method'], ['S256'])
        self.assertEqual(qs['state'], ['state123'])


class TokenCryptoTests(unittest.TestCase):
    def test_round_trip_and_wrong_key(self):
        key = auth.Fernet.generate_key().decode('ascii')
        encrypted = auth.encrypt_token(key, 'secret-token')
        self.assertNotIn('secret-token', encrypted)
        self.assertEqual(auth.decrypt_token(key, encrypted), 'secret-token')
        other_key = auth.Fernet.generate_key().decode('ascii')
        with self.assertRaises(auth.AuthError):
            auth.decrypt_token(other_key, encrypted)


class OAuthApiTests(unittest.TestCase):
    def test_exchange_code_posts_pkce_payload(self):
        with patch('chess_trainer.auth.requests.post') as post:
            post.return_value = FakeResponse(200, {
                'access_token': 'tok', 'token_type': 'Bearer', 'expires_in': 3600,
            })
            token = auth.exchange_code(
                client_id='cid', redirect_uri='http://localhost:23777/auth/callback',
                code='code1', code_verifier='verifier1',
            )
        self.assertEqual(token['access_token'], 'tok')
        _, kwargs = post.call_args
        self.assertEqual(kwargs['data']['grant_type'], 'authorization_code')
        self.assertEqual(kwargs['data']['code_verifier'], 'verifier1')
        self.assertEqual(kwargs['data']['client_id'], 'cid')

    def test_exchange_code_error_raises_auth_error(self):
        with patch('chess_trainer.auth.requests.post') as post:
            post.return_value = FakeResponse(400, {'error': 'invalid_grant'})
            with self.assertRaises(auth.AuthError):
                auth.exchange_code(client_id='cid', redirect_uri='uri', code='x', code_verifier='v')

    def test_fetch_account_uses_bearer_header(self):
        with patch('chess_trainer.auth.requests.get') as get:
            get.return_value = FakeResponse(200, {'id': 'abc', 'username': 'Tester'})
            account = auth.fetch_account('tok123')
        self.assertEqual(account['username'], 'Tester')
        _, kwargs = get.call_args
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer tok123')

    def test_fetch_account_401_raises(self):
        with patch('chess_trainer.auth.requests.get') as get:
            get.return_value = FakeResponse(401, {})
            with self.assertRaises(auth.AuthError):
                auth.fetch_account('bad-token')


class UserStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / 'history.sqlite3'
        self.key = auth.Fernet.generate_key().decode('ascii')
        self.store = UserStore(self.db, self.key)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_migration_adds_users_and_presets_tables(self):
        store = PuzzleHistoryStore(self.db)
        self.assertEqual(SCHEMA_VERSION, 9)
        self.assertEqual(store.schema_version, 9)
        with sqlite3.connect(self.db) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn('users', tables)
            self.assertIn('puzzle_presets', tables)
            attempt_cols = {row[1] for row in connection.execute('PRAGMA table_info(puzzle_attempts)')}
            session_cols = {row[1] for row in connection.execute('PRAGMA table_info(puzzle_sessions)')}
            user_cols = {row[1] for row in connection.execute('PRAGMA table_info(users)')}
        self.assertIn('user_id', attempt_cols)
        self.assertIn('user_id', session_cols)
        self.assertIn('presets_seeded', user_cols)

    def test_token_is_encrypted_at_rest_and_decrypted_on_read(self):
        user = self.store.save_lichess_account(
            lichess_id='li-1', username='Tester', access_token='plain-token',
        )
        self.assertEqual(user['access_token'], 'plain-token')
        with sqlite3.connect(self.db) as connection:
            raw = connection.execute(
                'SELECT access_token FROM users WHERE lichess_id = ?', ('li-1',)
            ).fetchone()[0]
        self.assertNotIn('plain-token', raw)
        fetched = self.store.get_user(user['id'])
        self.assertEqual(fetched['username'], 'Tester')
        self.assertEqual(fetched['access_token'], 'plain-token')

    def test_reauthorization_keeps_user_id_and_refreshes_token(self):
        first = self.store.save_lichess_account(lichess_id='li-1', username='T', access_token='tok-a')
        second = self.store.save_lichess_account(lichess_id='li-1', username='T', access_token='tok-b')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(self.store.get_user(first['id'])['access_token'], 'tok-b')

    def test_mark_token_invalid_empties_token(self):
        user = self.store.save_lichess_account(lichess_id='li-1', username='T', access_token='tok')
        self.store.mark_token_invalid(user['id'])
        self.assertEqual(self.store.get_user(user['id'])['access_token'], '')

    def test_claim_legacy_history_is_one_time(self):
        history = PuzzleHistoryStore(self.db)
        history.record_attempt(
            puzzle_id='legacy', rating=1200, success=True,
            fen='f', orientation='white', moves=[],
        )
        first = self.store.save_lichess_account(lichess_id='li-1', username='A', access_token='t1')
        claimed = self.store.claim_legacy_history(first['id'])
        self.assertEqual(claimed['attempts'], 1)
        self.assertEqual(
            [e['puzzle_id'] for e in history.get_history(user_id=first['id'])], ['legacy']
        )
        second = self.store.save_lichess_account(lichess_id='li-2', username='B', access_token='t2')
        self.assertEqual(self.store.claim_legacy_history(second['id'])['attempts'], 0)


class PerUserHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = PuzzleHistoryStore(Path(self.tempdir.name) / 'h.sqlite3')

    def tearDown(self):
        self.tempdir.cleanup()

    def test_history_and_stats_scoped_per_user(self):
        self.store.record_attempt(
            puzzle_id='a', rating=1000, success=True, fen='f', orientation='white', moves=[], user_id='u1',
        )
        self.store.record_attempt(
            puzzle_id='b', rating=1500, success=False, fen='f', orientation='black', moves=[], user_id='u2',
        )
        self.assertEqual([e['puzzle_id'] for e in self.store.get_history(user_id='u1')], ['a'])
        self.assertEqual([e['puzzle_id'] for e in self.store.get_history(user_id='u2')], ['b'])
        stats_u1 = self.store.get_stats(user_id='u1')
        self.assertEqual(stats_u1['completed'], 1)
        self.assertEqual(stats_u1['successes'], 1)
        self.assertEqual(self.store.get_stats(user_id='u2')['successes'], 0)

    def test_sessions_scoped_per_user(self):
        s1 = self.store.start_session(30, user_id='u1')
        self.store.start_session(10, user_id='u2')
        self.store.end_session(s1['id'])
        self.assertEqual([s['id'] for s in self.store.get_sessions(user_id='u1')], [s1['id']])
        self.assertEqual(self.store.get_sessions(user_id='u2'), [])


class AppAuthFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        env = root / '.env'
        env.write_text('LICHESS_CLIENT_ID=test-client\n')
        self.app_wrapper = ChessApp(env_path=str(env), database_path=str(root / 'history.sqlite3'))
        self.client = self.app_wrapper.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def _sign_in(self, lichess_id='li-1', username='Tester', token='tok'):
        user = self.app_wrapper.user_store.save_lichess_account(
            lichess_id=lichess_id, username=username, access_token=token,
        )
        with self.client.session_transaction() as sess:
            sess['user_id'] = user['id']
        return user

    def _login_state(self):
        self.client.get('/auth/login')
        with self.client.session_transaction() as sess:
            return sess['oauth_state']

    def test_api_me_anonymous(self):
        response = self.client.get('/api/me')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json['user'])

    def test_puzzle_endpoints_require_sign_in(self):
        response = self.client.post('/api/puzzles/start', json={'mode': 'normal', 'blindfold_depth': 9})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json['error'], 'sign_in_required')
        self.assertEqual(response.json['login_url'], '/auth/login')
        self.assertEqual(self.client.get('/api/board/puzzles').status_code, 401)
        self.assertEqual(self.client.get('/api/puzzles/history').status_code, 401)
        # Boards that never call Lichess stay open to anonymous visitors.
        self.assertEqual(self.client.get('/api/board/analysis').status_code, 200)

    def test_login_redirects_to_lichess_with_pkce(self):
        response = self.client.get('/auth/login')
        self.assertEqual(response.status_code, 302)
        location = urlparse(response.headers['Location'])
        self.assertEqual(location.scheme, 'https')
        self.assertEqual(location.netloc, 'lichess.org')
        self.assertEqual(location.path, '/oauth')
        qs = parse_qs(location.query)
        self.assertEqual(qs['client_id'], ['test-client'])
        self.assertEqual(qs['code_challenge_method'], ['S256'])
        self.assertEqual(qs['redirect_uri'], ['http://localhost:23777/auth/callback'])
        with self.client.session_transaction() as sess:
            self.assertEqual(sess['oauth_state'], qs['state'][0])
            self.assertTrue(sess['oauth_verifier'])

    def test_login_without_client_id_fails(self):
        self.app_wrapper.auth_config.lichess_client_id = None
        self.assertEqual(self.client.get('/auth/login').status_code, 500)

    def test_login_from_ip_starts_session_on_callback_host(self):
        response = self.client.get('/auth/login', base_url='http://127.0.0.1:23777')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], 'http://localhost:23777/auth/login')
        self.assertNotIn('Set-Cookie', response.headers)
        response = self.client.get(response.headers['Location'])
        state = parse_qs(urlparse(response.headers['Location']).query)['state'][0]
        with patch('chess_trainer.app.exchange_code', return_value={'access_token': 'tok'}), \
                patch('chess_trainer.app.fetch_account', return_value={'id': 'li-1', 'username': 'Tester'}):
            response = self.client.get(
                f'http://localhost:23777/auth/callback?code=abc&state={state}'
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('http://localhost:23777/api/me').json['user']['username'], 'Tester')

    def test_callback_rejects_bad_state(self):
        self.client.get('/auth/login')
        response = self.client.get('/auth/callback?code=abc&state=wrong')
        self.assertEqual(response.status_code, 400)

    def test_callback_exchanges_code_and_signs_in(self):
        state = self._login_state()
        with patch('chess_trainer.app.exchange_code', return_value={
            'access_token': 'tok-123', 'token_type': 'Bearer', 'expires_in': 3600,
        }), patch('chess_trainer.app.fetch_account', return_value={'id': 'li-9', 'username': 'Newbie'}):
            response = self.client.get(f'/auth/callback?code=abc&state={state}')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/blind_puzzles')
        user = self.app_wrapper.user_store.get_user_by_lichess_id('li-9')
        self.assertEqual(user['username'], 'Newbie')
        self.assertEqual(user['access_token'], 'tok-123')
        self.assertIsNotNone(user['expires_at'])
        response = self.client.get('/api/me')
        self.assertEqual(response.json['user']['username'], 'Newbie')

    def test_callback_oauth_error_param(self):
        self.client.get('/auth/login')
        response = self.client.get('/auth/callback?error=access_denied')
        self.assertEqual(response.status_code, 400)

    def test_callback_claims_legacy_history(self):
        history = self.app_wrapper.puzzle_history
        history.record_attempt(
            puzzle_id='legacy', rating=1200, success=True, fen='f', orientation='white', moves=[],
        )
        state = self._login_state()
        with patch('chess_trainer.app.exchange_code', return_value={'access_token': 't'}), \
                patch('chess_trainer.app.fetch_account', return_value={'id': 'li-9', 'username': 'N'}):
            self.client.get(f'/auth/callback?code=abc&state={state}')
        user = self.app_wrapper.user_store.get_user_by_lichess_id('li-9')
        self.assertEqual([e['puzzle_id'] for e in history.get_history(user_id=user['id'])], ['legacy'])

    def test_logout_clears_session_and_revokes_token(self):
        self._sign_in(token='tok-to-revoke')
        with patch('chess_trainer.app.revoke_token') as revoke:
            response = self.client.post('/auth/logout')
        self.assertEqual(response.status_code, 200)
        revoke.assert_called_once_with('tok-to-revoke')
        self.assertIsNone(self.client.get('/api/me').json['user'])
        response = self.client.post('/api/puzzles/start', json={'mode': 'normal', 'blindfold_depth': 9})
        self.assertEqual(response.status_code, 401)

    def test_start_puzzle_uses_per_user_board_and_session(self):
        user = self._sign_in()

        class FakeBoard:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.current_puzzle_id = 'p1'
                self.rating = 1000
                self.puzzle_start_fen = 'f'
                self.orientation = 'white'
                self.puzzle_start_moves = []

        with patch('chess_trainer.app.BlindfoldPuzzleChessBoard', FakeBoard):
            response = self.client.post('/api/puzzles/start', json={
                'mode': 'normal', 'blindfold_depth': 9, 'timed': True, 'duration_minutes': 30,
            })
        self.assertEqual(response.status_code, 200)
        board = self.app_wrapper._boards[(user['id'], 'puzzles')]
        self.assertEqual(board.kwargs['user_id'], user['id'])
        session = self.app_wrapper.puzzle_history.get_session(response.json['session']['id'])
        self.assertEqual(session['user_id'], user['id'])

    def test_history_api_is_scoped_to_signed_in_user(self):
        user = self._sign_in(lichess_id='li-1', username='A', token='t1')
        other = self.app_wrapper.user_store.save_lichess_account(lichess_id='li-2', username='B', access_token='t2')
        history = self.app_wrapper.puzzle_history
        history.record_attempt(
            puzzle_id='mine', rating=1000, success=True, fen='f', orientation='white', moves=[], user_id=user['id'],
        )
        history.record_attempt(
            puzzle_id='theirs', rating=1000, success=True, fen='f', orientation='white', moves=[], user_id=other['id'],
        )
        response = self.client.get('/api/puzzles/history')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([e['puzzle_id'] for e in response.json['history']], ['mine'])
        self.assertEqual(response.json['stats']['completed'], 1)

    def test_safe_next_accepts_only_local_paths(self):
        safe = ChessApp._safe_next
        self.assertEqual(safe('/blind_puzzles'), '/blind_puzzles')
        self.assertEqual(safe('/blind_puzzles/play?session_id=abc'), '/blind_puzzles/play?session_id=abc')
        for bad in (None, '', 'evil.com', '//evil.com', '/\\evil.com',
                    'https://evil.com', 'http://localhost:23777/blind_puzzles', '  /x'):
            self.assertIsNone(safe(bad), bad)

    def test_gated_pages_show_sign_in_wall_when_anonymous(self):
        for url in ('/blind_puzzles', '/blind_puzzles/play?session_id=abc', '/puzzle_history'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            body = response.get_data(as_text=True)
            self.assertIn('Sign in with Lichess', body)
            self.assertIn('sign-in-wall', body)
            # The dashboard / board markup must not leak to anonymous users.
            self.assertNotIn('puzzle-settings-dialog', body)
            self.assertNotIn('id="board"', body)
            self.assertNotIn('history-dashboard', body)
        # The wall offers to return to the exact page after signing in.
        body = self.client.get('/blind_puzzles/play?session_id=abc').get_data(as_text=True)
        self.assertIn('/auth/login?next=/blind_puzzles/play%3Fsession_id%3Dabc', body)

    def test_gated_pages_render_normally_when_signed_in(self):
        self._sign_in()
        body = self.client.get('/blind_puzzles').get_data(as_text=True)
        self.assertIn('puzzle-settings-dialog', body)
        self.assertNotIn('sign-in-wall', body)
        body = self.client.get('/blind_puzzles/play?session_id=abc').get_data(as_text=True)
        self.assertIn('id="board"', body)
        self.assertNotIn('sign-in-wall', body)

    def test_login_stores_next_and_callback_redirects_to_it(self):
        self.client.get('/auth/login?next=/blind_puzzles/play%3Fsession_id%3Dabc')
        with self.client.session_transaction() as sess:
            self.assertEqual(sess['post_login_next'], '/blind_puzzles/play?session_id=abc')
            state = sess['oauth_state']
        with patch('chess_trainer.app.exchange_code', return_value={'access_token': 'tok'}), \
                patch('chess_trainer.app.fetch_account', return_value={'id': 'li-1', 'username': 'Tester'}):
            response = self.client.get(f'/auth/callback?code=abc&state={state}')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/blind_puzzles/play?session_id=abc')

    def test_callback_ignores_unsafe_next(self):
        for bad in ('//evil.com', 'https://evil.com'):
            self.client.get(f'/auth/login?next={bad}')
            with self.client.session_transaction() as sess:
                self.assertNotIn('post_login_next', sess)
                state = sess['oauth_state']
            with patch('chess_trainer.app.exchange_code', return_value={'access_token': 'tok'}), \
                    patch('chess_trainer.app.fetch_account', return_value={'id': 'li-1', 'username': 'Tester'}):
                response = self.client.get(f'/auth/callback?code=abc&state={state}')
            self.assertEqual(response.headers['Location'], '/blind_puzzles')

    def test_login_from_ip_preserves_next_in_host_redirect(self):
        response = self.client.get('/auth/login?next=/blind_puzzles', base_url='http://127.0.0.1:23777')
        self.assertEqual(response.status_code, 302)
        location = urlparse(response.headers['Location'])
        self.assertEqual((location.scheme, location.netloc, location.path),
                         ('http', 'localhost:23777', '/auth/login'))
        self.assertEqual(parse_qs(location.query)['next'], ['/blind_puzzles'])


if __name__ == '__main__':
    unittest.main()
