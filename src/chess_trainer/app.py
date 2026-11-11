'''Flask application for Chess Trainer.'''
from datetime import datetime, timedelta, timezone
import secrets
from urllib.parse import quote, urlsplit, urlunsplit

from flask import Flask, jsonify, redirect, render_template, request
from flask import session as flask_session
from berserk import Client, TokenSession
from berserk.exceptions import ResponseError

from .auth import (
    AuthError,
    build_authorize_url,
    exchange_code,
    fetch_account,
    generate_pkce_pair,
    load_auth_config,
    revoke_token,
)
from .boards import ChessBoard, board_mapping
from .boards.puzzle_board import BlindfoldPuzzleChessBoard
from .boards.puzzle_mix import DIFFICULTY_LABELS, DIFFICULTY_ORDER, PuzzleMix
from .puzzle_history import PuzzleHistoryStore
from .user_store import UserStore


# ---------- Dashboard presentation data -------------------------------------
# Dated dashboard data is grouped by UTC day; individual timestamps stay
# semantic (<time datetime>) so the browser can localize them.

OUTCOME_LABELS = {'success': 'Solved', 'failure': 'Missed', 'expired': 'Expired'}


def _utc_day(iso_timestamp: str) -> str:
    '''Return the UTC calendar day (YYYY-MM-DD) of an ISO timestamp.'''
    moment = datetime.fromisoformat(iso_timestamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).date().isoformat()


def _format_duration_ms(duration_ms: int | None) -> str:
    if not duration_ms:
        return ''
    minutes, seconds = divmod(round(duration_ms / 1000), 60)
    if minutes >= 60:
        return f'{minutes // 60}h {minutes % 60}m'
    return f'{minutes}:{seconds:02d}'


def _entry_card(entry: dict) -> dict:
    '''View model for one puzzle history card.'''
    outcome = entry.get('outcome') or ('success' if entry['success'] else 'failure')
    label = OUTCOME_LABELS.get(outcome, outcome)
    completed_at = entry['completed_at']
    return {
        'url': 'https://lichess.org/training/' + quote(str(entry['puzzle_id']), safe=''),
        'aria_label': f'{label} puzzle rated {entry["rating"]} at {completed_at}. Open on Lichess.',
        'fen': entry['fen'],
        'orientation': entry['orientation'],
        'outcome': outcome,
        'outcome_label': label,
        'rating': entry['rating'],
        'completed_at': completed_at,
        'duration_text': _format_duration_ms(entry.get('duration_ms')),
    }


def _mix_label(profile: dict | None, fallback_mode: str | None = None,
               fallback_depth: int | None = None) -> str:
    '''Human summary of a sampling profile, mirroring the settings preview.'''
    profile = profile or {}
    difficulties = profile.get('difficulties') or ([fallback_mode] if fallback_mode else [])
    depth_min = profile.get('depth_min', fallback_depth)
    depth_max = profile.get('depth_max', depth_min)
    if len(difficulties) == len(DIFFICULTY_ORDER):
        difficulty = 'all difficulties'
    else:
        difficulty = ', '.join(DIFFICULTY_LABELS.get(mode, mode) for mode in difficulties)
    depth = f'depth {depth_min}' if depth_min == depth_max else f'depth {depth_min}–{depth_max}'
    return f'{difficulty} · {depth}'


def _session_group(session: dict, entries: list[dict]) -> dict:
    '''View model for one timed-session frame in the history.'''
    expired = max(
        session.get('expired_count', 0),
        sum(1 for entry in entries if entry['outcome'] == 'expired'),
    )
    return {
        'id': session['id'],
        'title': f"{round(session['duration_seconds'] / 60)} minute session",
        'mix_label': _mix_label(
            session.get('profile'), session.get('mode'), session.get('blindfold_depth')
        ),
        'started_at': session['started_at'],
        'ended_at': session.get('ended_at'),
        'stats_text': (
            f"{session['completed_count']} completed · {session['successes']} solved · "
            f"{session['failures']} missed · {expired} timed out"
        ),
        'entries': entries,
    }


def _group_history_by_utc_day(history: list[dict], sessions: list[dict]) -> list[dict]:
    '''Group attempts and sessions into UTC days, newest first.'''
    sessions_by_id = {session['id']: session for session in sessions}
    days: dict[str, dict] = {}

    def day_bucket(day: str) -> dict:
        return days.setdefault(day, {'date': day, 'entries': [], 'sessions': {}})

    for session in sessions:
        bucket = day_bucket(_utc_day(session['started_at']))
        bucket['sessions'][session['id']] = _session_group(session, [])
    for entry in history:
        session_id = entry.get('session_id')
        if session_id and session_id in sessions_by_id:
            session = sessions_by_id[session_id]
            bucket = day_bucket(_utc_day(session['started_at']))
            bucket['sessions'][session_id]['entries'].append(_entry_card(entry))
        else:
            day_bucket(_utc_day(entry['completed_at']))['entries'].append(_entry_card(entry))

    grouped = []
    for day in sorted(days, reverse=True):
        bucket = days[day]
        session_groups = sorted(
            bucket['sessions'].values(), key=lambda group: group['started_at'], reverse=True
        )
        count = len(bucket['entries']) + sum(len(group['entries']) for group in session_groups)
        grouped.append({
            'date': day,
            'entries': bucket['entries'],
            'sessions': session_groups,
            'count': count,
        })
    return grouped


class ChessApp:
    def __init__(self, env_path: str = '.env', database_path: str | None = None):
        self.app = Flask(__name__)
        self.auth_config = load_auth_config(env_path)
        self.app.secret_key = self.auth_config.secret_key
        self.app.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE='Lax',
        )
        # Boards are cached per (user_id, board_id); anonymous visitors share
        # the None user and may only use boards that never call Lichess.
        self._boards: dict[tuple[str | None, str], ChessBoard] = {}
        # Per-user berserk clients, keyed by local user id.
        self._clients: dict[str, tuple[Client, str]] = {}
        database_path = database_path or self.app.instance_path + '/puzzle_history.sqlite3'
        self.puzzle_history = PuzzleHistoryStore(database_path)
        self.user_store = UserStore(database_path, self.auth_config.token_encryption_key)
        self._setup_routes()

    def _setup_routes(self):
        # Pages
        self.app.add_url_rule('/', 'index', self.index)
        self.app.add_url_rule('/analysis', 'analysis', self.analysis)
        self.app.add_url_rule('/blind_puzzles', 'blind_puzzles', self.blind_puzzles)
        self.app.add_url_rule('/blind_puzzles/play', 'blind_puzzles_play', self.blind_puzzles_play)
        self.app.add_url_rule('/puzzle_history', 'puzzle_history', self.puzzle_history_page)
        self.app.add_url_rule('/api/puzzles/start', 'api_start_puzzle', self.api_start_puzzle, methods=['POST'])
        self.app.add_url_rule('/openings', 'openings', self.openings)

        # Auth
        self.app.add_url_rule('/auth/login', 'auth_login', self.auth_login, methods=['GET'])
        self.app.add_url_rule('/auth/callback', 'auth_callback', self.auth_callback, methods=['GET'])
        self.app.add_url_rule('/auth/logout', 'auth_logout', self.auth_logout, methods=['POST'])
        self.app.add_url_rule('/api/me', 'api_me', self.api_me, methods=['GET'])

        # API
        self.app.add_url_rule('/api/board/<string:board_id>', 'api_get_board', self.api_get_board, methods=['GET'])
        self.app.add_url_rule('/api/puzzles/history', 'api_puzzle_history', self.api_puzzle_history, methods=['GET'])
        self.app.add_url_rule('/api/puzzles/presets', 'api_list_presets', self.api_list_presets, methods=['GET'])
        self.app.add_url_rule('/api/puzzles/presets', 'api_create_preset', self.api_create_preset, methods=['POST'])
        self.app.add_url_rule('/api/puzzles/presets/<string:preset_id>', 'api_delete_preset', self.api_delete_preset, methods=['DELETE'])
        self.app.add_url_rule('/api/puzzles/session/<string:session_id>', 'api_puzzle_session', self.api_puzzle_session, methods=['GET'])
        self.app.add_url_rule('/api/puzzles/session/<string:session_id>/finish', 'api_finish_puzzle_session', self.api_finish_puzzle_session, methods=['POST'])
        self.app.add_url_rule('/api/puzzles/session/<string:session_id>/detach', 'api_detach_puzzle_session', self.api_detach_puzzle_session, methods=['POST'])
        self.app.add_url_rule('/api/move/<string:board_id>', 'api_move', self.api_move, methods=['POST'])
        self.app.add_url_rule('/api/goto/<string:board_id>/<int:ply>', 'api_goto', self.api_goto, methods=['GET'])
        self.app.add_url_rule('/api/undo/<string:board_id>', 'api_undo', self.api_undo, methods=['POST'])
        self.app.add_url_rule('/api/reset/<string:board_id>', 'api_reset', self.api_reset, methods=['POST'])
        self.app.add_url_rule('/api/fen/<string:board_id>', 'api_set_fen', self.api_set_fen, methods=['POST'])

    # ---------- Pages ----------------------------------------------------------

    def index(self):
        '''Menu / landing page.'''
        return render_template('index.html')

    def analysis(self):
        '''Analysis board page.'''
        return render_template('analysis.html')

    def blind_puzzles(self):
        '''Blindfold puzzle landing page and history dashboard.'''
        user = self._current_user()
        if user is None:
            return self._sign_in_wall()
        return self._render_puzzle_dashboard(user)

    def blind_puzzles_play(self):
        '''Active blindfold puzzle board.'''
        if self._current_user() is None:
            return self._sign_in_wall()
        return render_template('blind_puzzles.html')

    def puzzle_history_page(self):
        '''Legacy URL for the blindfold puzzle dashboard.'''
        user = self._current_user()
        if user is None:
            return self._sign_in_wall()
        return self._render_puzzle_dashboard(user)

    def _render_puzzle_dashboard(self, user: dict):
        '''Render the blindfold dashboard with presentation data prepared here.'''
        user_id = user['id']
        history = self.puzzle_history.get_history(limit=500, user_id=user_id)
        sessions = self.puzzle_history.get_sessions(user_id=user_id)
        if history:
            empty_message = ''
        elif sessions:
            empty_message = 'No completed puzzles yet. Timed sessions will appear here when they finish.'
        else:
            empty_message = 'No completed puzzles yet. Finish a blindfold puzzle and it will show up here.'
        return render_template(
            'puzzle_history.html',
            progress=self.puzzle_history.get_progress(user_id=user_id),
            days=_group_history_by_utc_day(history, sessions),
            empty_message=empty_message,
        )

    def openings(self):
        '''Openings trainer page.'''
        return render_template('openings.html')

    # ---------- Auth -----------------------------------------------------------

    def _current_user(self) -> dict | None:
        '''Return the signed-in user row, or None when anonymous/tokenless.'''
        user_id = flask_session.get('user_id')
        if not user_id:
            return None
        user = self.user_store.get_user(user_id)
        if user is None or not user['access_token']:
            return None
        return user

    def _sign_in_required(self):
        return jsonify(ok=False, error='sign_in_required', login_url='/auth/login'), 401

    @staticmethod
    def _safe_next(value) -> str | None:
        '''Return a local path usable as a post-login redirect, else None.'''
        if not value or not isinstance(value, str):
            return None
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or not value.startswith('/'):
            return None
        if value.startswith('//') or value.startswith('/\\'):
            return None
        return value

    def _sign_in_wall(self):
        '''Minimal sign-in page for fully gated pages (see _guidelines/_not_authentificated.md).'''
        next_path = request.full_path
        if next_path.endswith('?'):
            next_path = next_path[:-1]
        return render_template('sign_in.html', next=next_path)

    def _lichess_client(self, user: dict) -> tuple[Client, str]:
        '''Return the cached berserk client and decrypted token for a user.'''
        cached = self._clients.get(user['id'])
        if cached is None:
            token = user['access_token']
            cached = (Client(TokenSession(token)), token)
            self._clients[user['id']] = cached
        return cached

    def auth_login(self):
        '''Start the Lichess OAuth2 authorization-code flow with PKCE.'''
        config = self.auth_config
        if not config.lichess_client_id:
            return jsonify(ok=False, error='LICHESS_CLIENT_ID is not configured on this server.'), 500
        # Start on the callback's host so the browser sends the same session
        # cookie back (localhost and 127.0.0.1 have separate cookie jars).
        # Keep the query string so ?next= survives the host normalization.
        callback = urlsplit(config.lichess_redirect_uri)
        current = urlsplit(request.url)
        if (current.scheme, current.hostname) != (callback.scheme, callback.hostname):
            return redirect(urlunsplit((
                callback.scheme, callback.netloc, '/auth/login',
                request.query_string.decode('ascii', 'replace'), '',
            )))
        next_url = self._safe_next(request.args.get('next'))
        if next_url:
            flask_session['post_login_next'] = next_url
        else:
            flask_session.pop('post_login_next', None)
        verifier, challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(24)
        flask_session['oauth_state'] = state
        flask_session['oauth_verifier'] = verifier
        flask_session.permanent = True
        return redirect(build_authorize_url(
            config.lichess_client_id,
            config.lichess_redirect_uri,
            config.lichess_scopes,
            state,
            challenge,
        ))

    def auth_callback(self):
        '''Handle the Lichess redirect: verify state, exchange the code, sign in.'''
        if request.args.get('error'):
            return jsonify(ok=False, error=f"Lichess authorization failed: {request.args['error']}"), 400
        state = request.args.get('state', '')
        code = request.args.get('code', '')
        if not state or state != flask_session.pop('oauth_state', None):
            return jsonify(ok=False, error='Invalid OAuth state. Please try signing in again.'), 400
        verifier = flask_session.pop('oauth_verifier', None)
        if not code or not verifier:
            return jsonify(ok=False, error='Missing authorization code.'), 400
        config = self.auth_config
        try:
            token = exchange_code(
                client_id=config.lichess_client_id,
                redirect_uri=config.lichess_redirect_uri,
                code=code,
                code_verifier=verifier,
            )
            account = fetch_account(token['access_token'])
        except AuthError as error:
            self.app.logger.warning('Lichess OAuth callback failed: %s', error)
            return jsonify(ok=False, error='Could not sign in with Lichess. Please try again.'), 502

        expires_at = None
        if token.get('expires_in'):
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=int(token['expires_in']))
            ).isoformat()
        user = self.user_store.save_lichess_account(
            lichess_id=str(account['id']),
            username=account.get('username') or str(account['id']),
            access_token=token['access_token'],
            token_type=token.get('token_type', 'Bearer'),
            expires_at=expires_at,
        )
        # One-time: history recorded before auth existed belongs to the
        # first account that signs in on this machine.
        self.user_store.claim_legacy_history(user['id'])
        self._clients.pop(user['id'], None)
        # Read before the clear: the wall page stored where to return to.
        next_url = self._safe_next(flask_session.pop('post_login_next', None))
        flask_session.clear()
        flask_session['user_id'] = user['id']
        flask_session.permanent = True
        return redirect(next_url or '/blind_puzzles')

    def auth_logout(self):
        '''Clear the local session and best-effort revoke the Lichess token.'''
        user = self._current_user()
        flask_session.clear()
        if user is not None:
            self._clients.pop(user['id'], None)
            try:
                revoke_token(user['access_token'])
            except AuthError:
                self.app.logger.info('Could not revoke the Lichess token on logout.')
        return jsonify(ok=True)

    def api_me(self):
        user = self._current_user()
        if user is None:
            return jsonify(user=None)
        return jsonify(user={
            'id': user['id'],
            'username': user['username'],
            'lichess_id': user['lichess_id'],
        })

    # ---------- API ------------------------------------------------------------

    def _get_board(self, board_id: str = 'default') -> ChessBoard | None:
        '''Return the board for the current user, or None when sign-in is required.'''
        user = self._current_user()
        if board_id == 'puzzles' and user is None:
            return None
        key = (user['id'] if user else None, board_id)
        if key not in self._boards:
            board_class = board_mapping.get(board_id, ChessBoard)
            board_options: dict = {}
            if user is not None:
                client, token = self._lichess_client(user)
                board_options = {'client': client, 'token': token}
            if board_id == 'puzzles':
                board_options['history_store'] = self.puzzle_history
                board_options['user_id'] = user['id']
            self._boards[key] = board_class(**board_options)
        return self._boards[key]

    def api_puzzle_history(self):
        user = self._current_user()
        if user is None:
            return self._sign_in_required()
        return jsonify(
            stats=self.puzzle_history.get_stats(user_id=user['id']),
            progress=self.puzzle_history.get_progress(user_id=user['id']),
            history=self.puzzle_history.get_history(limit=500, user_id=user['id']),
            sessions=self.puzzle_history.get_sessions(user_id=user['id']),
        )

    def api_list_presets(self):
        user = self._current_user()
        if user is None:
            return self._sign_in_required()
        self.puzzle_history.ensure_default_preset(user['id'])
        return jsonify(presets=self.puzzle_history.list_presets(user['id']))

    def api_create_preset(self):
        user = self._current_user()
        if user is None:
            return self._sign_in_required()
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or '').strip()
        if not name or len(name) > 40:
            return jsonify(ok=False, error='Preset name must be 1–40 characters.'), 400
        try:
            mix = PuzzleMix.from_payload(data)
        except ValueError as error:
            return jsonify(ok=False, error=str(error)), 400
        preset = self.puzzle_history.save_preset(
            user_id=user['id'], name=name, profile=mix.to_payload(),
        )
        return jsonify(ok=True, preset=preset), 201

    def api_delete_preset(self, preset_id: str):
        user = self._current_user()
        if user is None:
            return self._sign_in_required()
        if not self.puzzle_history.delete_preset(preset_id, user['id']):
            return jsonify(ok=False, error='Preset not found.'), 404
        return jsonify(ok=True)

    def _owned_session(self, session_id: str) -> dict | None:
        '''Return the session only if it belongs to the signed-in user.'''
        user = self._current_user()
        if user is None:
            return None
        session = self.puzzle_history.get_session(session_id)
        if session is None or session.get('user_id') != user['id']:
            return None
        return session

    def api_puzzle_session(self, session_id: str):
        if self._current_user() is None:
            return self._sign_in_required()
        session = self._owned_session(session_id)
        if session is None:
            return jsonify(ok=False, error='Timed session not found.'), 404
        return jsonify(session=session)

    def api_finish_puzzle_session(self, session_id: str):
        if self._current_user() is None:
            return self._sign_in_required()
        session = self.puzzle_history.end_session(session_id)
        if session is None or session.get('user_id') != self._current_user()['id']:
            return jsonify(ok=False, error='Timed session not found.'), 404
        return jsonify(session=session)

    def api_detach_puzzle_session(self, session_id: str):
        '''End a timed session and switch the board to normal untimed training.'''
        user = self._current_user()
        if user is None:
            return self._sign_in_required()
        session = self.puzzle_history.detach_session(session_id)
        if session is None or session.get('user_id') != user['id']:
            return jsonify(ok=False, error='Timed session not found.'), 404
        board = self._boards.get((user['id'], 'puzzles'))
        if board is not None and getattr(board, 'session_id', None) == session_id:
            board.session_id = None
        return jsonify(session=session)

    def _active_session(self, session_id: str) -> dict | None:
        '''Return the session only while it is still running.'''
        session = self.puzzle_history.get_session(session_id)
        if session is None or session['status'] != 'active' or session['remaining_seconds'] <= 0:
            return None
        return session

    def api_start_puzzle(self):
        user = self._current_user()
        if user is None:
            return self._sign_in_required()
        data = request.get_json(silent=True) or {}
        try:
            mix = PuzzleMix.from_payload(data)
        except ValueError as error:
            return jsonify(ok=False, error=str(error)), 400

        timed = data.get('timed', False)
        duration_minutes = data.get('duration_minutes', 30)
        if not isinstance(timed, bool):
            return jsonify(ok=False, error='Timer setting must be enabled or disabled.'), 400
        if timed and (
            isinstance(duration_minutes, bool)
            or not isinstance(duration_minutes, int)
            or not 1 <= duration_minutes <= 180
        ):
            return jsonify(ok=False, error='Session timer must be between 1 and 180 minutes.'), 400

        try:
            client, token = self._lichess_client(user)
            board = BlindfoldPuzzleChessBoard(
                client=client,
                token=token,
                mix=mix,
                history_store=self.puzzle_history,
                user_id=user['id'],
            )
        except ResponseError as error:
            if error.status_code in (401, 403):
                # The stored Lichess token was rejected: force a fresh sign-in.
                self.user_store.mark_token_invalid(user['id'])
                self._clients.pop(user['id'], None)
                flask_session.clear()
                return self._sign_in_required()
            self.app.logger.exception('Could not start a blindfold puzzle')
            return jsonify(ok=False, error='Could not fetch a puzzle right now. Please try again.'), 503
        except Exception:
            self.app.logger.exception('Could not start a blindfold puzzle')
            return jsonify(ok=False, error='Could not fetch a puzzle right now. Please try again.'), 503

        session = None
        if timed:
            try:
                session = self.puzzle_history.start_session(duration_minutes, mix=mix, user_id=user['id'])
            except Exception:
                self.app.logger.exception('Could not create a timed puzzle session')
                return jsonify(ok=False, error='Could not start a timed session. Please try again.'), 500
            board.session_id = session['id']
            saved = self.puzzle_history.set_session_puzzle(
                session['id'],
                puzzle_id=board.current_puzzle_id,
                rating=board.rating,
                fen=board.puzzle_start_fen,
                orientation=board.orientation,
                moves=board.puzzle_start_moves,
                mode=getattr(board, 'current_mode', None),
                blindfold_depth=getattr(board, 'current_blindfold_depth', None),
            )
            if not saved:
                self.puzzle_history.expire_session_if_due(session['id'])
                return jsonify(ok=False, error='The timed session ended before the puzzle could start.'), 410

        self._boards[(user['id'], 'puzzles')] = board
        redirect_url = '/blind_puzzles/play'
        if session:
            redirect_url += f"?session_id={session['id']}"
        return jsonify(ok=True, redirect_url=redirect_url, session=session, profile=mix.to_payload())

    def api_get_board(self, board_id: str):
        board = self._get_board(board_id)
        if board is None:
            return self._sign_in_required()
        session_id = getattr(board, 'session_id', None) if board_id == 'puzzles' else None
        session = self._active_session(session_id) if session_id else None
        session_active = session is not None if session_id else True
        return jsonify(
            fen=board.fen,
            turn=board.turn,
            legal_moves=board.legal_moves() if session_active else [],
            is_check=board.is_check,
            is_game_over=board.is_game_over,
            result=board.result,
            moves=board.move_history,
            orientation=getattr(board, 'orientation', 'white'),
            session_id=session_id,
            session_expired=not session_active,
        )

    def api_move(self, board_id: str):
        data = request.get_json(force=True)
        uci = data.get('uci', '')
        from_ply = data.get('from_ply', None)
        board = self._get_board(board_id)
        if board is None:
            return self._sign_in_required()
        session_id = getattr(board, 'session_id', None) if board_id == 'puzzles' else None
        if session_id and self._active_session(session_id) is None:
            return jsonify(
                legal=False,
                correct=False,
                completed=False,
                win=False,
                session_expired=True,
                fen=board.fen,
                turn=board.turn,
                legal_moves=[],
                is_check=board.is_check,
                is_game_over=board.is_game_over,
                result=board.result,
                moves=board.move_history,
                ply=board.total_plies,
                orientation=getattr(board, 'orientation', 'white'),
            )

        move_result = board.push_move_at_ply(uci, from_ply)
        ply = board.total_plies
        return jsonify(
            fen=board.fen,
            turn=board.turn,
            legal_moves=board.legal_moves() if not move_result.get('session_expired') else [],
            is_check=board.is_check,
            is_game_over=board.is_game_over,
            result=board.result,
            moves=board.move_history,
            ply=ply,
            orientation=getattr(board, 'orientation', 'white'),
            session_id=session_id,
            **move_result
        )

    def api_goto(self, board_id: str, ply: int):
        board = self._get_board(board_id)
        if board is None:
            return self._sign_in_required()
        session_id = getattr(board, 'session_id', None) if board_id == 'puzzles' else None
        if session_id and self._active_session(session_id) is None:
            return jsonify(ok=False, session_expired=True, error='Timed session has ended.'), 410
        pos = board.position_at_ply(ply)
        pos['moves'] = board.move_history
        pos['total_plies'] = board.total_plies
        pos['orientation'] = getattr(board, 'orientation', 'white')
        pos['session_id'] = session_id
        return jsonify(**pos)

    def api_undo(self, board_id: str):
        board = self._get_board(board_id)
        if board is None:
            return self._sign_in_required()
        undone = board.undo()
        return jsonify(
            undone=undone,
            fen=board.fen,
            turn=board.turn,
            legal_moves=board.legal_moves(),
            is_check=board.is_check,
            is_game_over=board.is_game_over,
            result=board.result,
            moves=board.move_history,
            orientation=getattr(board, 'orientation', 'white'),
        )

    def api_reset(self, board_id: str):
        board = self._get_board(board_id)
        if board is None:
            return self._sign_in_required()
        board.reset()
        return jsonify(
            fen=board.fen,
            turn=board.turn,
            legal_moves=board.legal_moves(),
            is_check=board.is_check,
            is_game_over=board.is_game_over,
            result=board.result,
            moves=board.move_history,
            orientation=getattr(board, 'orientation', 'white'),
        )

    def api_set_fen(self, board_id: str):
        data = request.get_json(force=True)
        fen = data.get('fen', '')
        board = self._get_board(board_id)
        if board is None:
            return self._sign_in_required()
        try:
            board.set_fen(fen)
        except ValueError:
            return jsonify(ok=False, error='Invalid FEN'), 400
        return jsonify(
            ok=True,
            fen=board.fen,
            turn=board.turn,
            legal_moves=board.legal_moves(),
            is_check=board.is_check,
            is_game_over=board.is_game_over,
            result=board.result,
            moves=board.move_history,
            orientation=getattr(board, 'orientation', 'white'),
        )

    # ---------- Entry point ------------------------------------------------------------

    def run(self):
        self.app.run(
            debug=True,
            port='23777'
        )
