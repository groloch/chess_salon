'''Flask application for Chess Trainer.'''
import dotenv

from flask import Flask, jsonify, redirect, render_template, request
from berserk import Client, TokenSession

from .boards import ChessBoard, board_mapping
from .boards.puzzle_board import BlindfoldPuzzleChessBoard, BlindfoldPuzzleConfig
from .puzzle_history import PuzzleHistoryStore


PUZZLE_DIFFICULTIES = {'easiest', 'easier', 'normal', 'harder', 'hardest'}


class ChessApp:
    def __init__(self):
        self.app = Flask(__name__)
        self._boards: dict[str, ChessBoard] = {}
        self.puzzle_history = PuzzleHistoryStore(
            self.app.instance_path + '/puzzle_history.sqlite3'
        )
        self._setup_routes()

        self.token = dotenv.get_key('.env', 'LICHESS_TOKEN')
        self.session = TokenSession(self.token)
        self.client = Client(self.session)

    def _setup_routes(self):
        # Pages
        self.app.add_url_rule('/', 'index', self.index)
        self.app.add_url_rule('/analysis', 'analysis', self.analysis)
        self.app.add_url_rule('/blind_puzzles', 'blind_puzzles', self.blind_puzzles)
        self.app.add_url_rule('/blind_puzzles/play', 'blind_puzzles_play', self.blind_puzzles_play)
        self.app.add_url_rule('/puzzle_history', 'puzzle_history', self.puzzle_history_page)
        self.app.add_url_rule('/api/puzzles/start', 'api_start_puzzle', self.api_start_puzzle, methods=['POST'])
        self.app.add_url_rule('/openings', 'openings', self.openings)

        # API
        self.app.add_url_rule('/api/board/<string:board_id>', 'api_get_board', self.api_get_board, methods=['GET'])
        self.app.add_url_rule('/api/puzzles/history', 'api_puzzle_history', self.api_puzzle_history, methods=['GET'])
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
        return render_template('puzzle_history.html')

    def blind_puzzles_play(self):
        '''Active blindfold puzzle board.'''
        return render_template('blind_puzzles.html')

    def puzzle_history_page(self):
        '''Legacy URL for the blindfold puzzle dashboard.'''
        return render_template('puzzle_history.html')

    def openings(self):
        '''Openings trainer page.'''
        return render_template('openings.html')

    # ---------- API ------------------------------------------------------------

    def _get_board(self, board_id: str = 'default') -> ChessBoard:
        if board_id not in self._boards:
            board_class = board_mapping.get(board_id, ChessBoard)
            board_options = {'client': self.client, 'token': self.token}
            if board_id == 'puzzles':
                board_options['history_store'] = self.puzzle_history
            self._boards[board_id] = board_class(**board_options)
        return self._boards[board_id]

    def api_puzzle_history(self):
        return jsonify(
            stats=self.puzzle_history.get_stats(),
            history=self.puzzle_history.get_history(limit=500),
            sessions=self.puzzle_history.get_sessions(),
        )

    def api_puzzle_session(self, session_id: str):
        session = self.puzzle_history.get_session(session_id)
        if session is None:
            return jsonify(ok=False, error='Timed session not found.'), 404
        return jsonify(session=session)

    def api_finish_puzzle_session(self, session_id: str):
        session = self.puzzle_history.end_session(session_id)
        if session is None:
            return jsonify(ok=False, error='Timed session not found.'), 404
        return jsonify(session=session)

    def api_detach_puzzle_session(self, session_id: str):
        '''End a timed session and switch the board to normal untimed training.'''
        session = self.puzzle_history.detach_session(session_id)
        if session is None:
            return jsonify(ok=False, error='Timed session not found.'), 404
        board = self._boards.get('puzzles')
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
        data = request.get_json(silent=True) or {}
        mode = data.get('mode', 'easiest')
        depth = data.get('blindfold_depth', 9)
        if not isinstance(mode, str) or mode not in PUZZLE_DIFFICULTIES:
            return jsonify(ok=False, error='Choose a valid puzzle difficulty.'), 400
        if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 40:
            return jsonify(ok=False, error='Blindfold depth must be a whole number from 1 to 40.'), 400

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
            board = BlindfoldPuzzleChessBoard(
                client=self.client,
                token=self.token,
                config=BlindfoldPuzzleConfig(mode=mode, blindfold_depth=depth),
                history_store=self.puzzle_history,
            )
        except Exception:
            self.app.logger.exception('Could not start a blindfold puzzle')
            return jsonify(ok=False, error='Could not fetch a puzzle right now. Please try again.'), 503

        session = None
        if timed:
            try:
                session = self.puzzle_history.start_session(
                    duration_minutes,
                    mode=mode,
                    blindfold_depth=depth,
                )
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
            )
            if not saved:
                self.puzzle_history.expire_session_if_due(session['id'])
                return jsonify(ok=False, error='The timed session ended before the puzzle could start.'), 410

        self._boards['puzzles'] = board
        redirect_url = '/blind_puzzles/play'
        if session:
            redirect_url += f"?session_id={session['id']}"
        return jsonify(ok=True, redirect_url=redirect_url, session=session)

    def api_get_board(self, board_id: str):
        board = self._get_board(board_id)
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
