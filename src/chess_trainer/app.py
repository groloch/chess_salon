"""Flask application for Chess Trainer."""
import dotenv

from flask import Flask, jsonify, render_template, request
from berserk import Client, TokenSession

from .boards import ChessBoard, board_mapping


class ChessApp:
    def __init__(self):
        self.app = Flask(__name__)
        self._boards: dict[str, ChessBoard] = {}
        self._setup_routes()

        self.token = dotenv.get_key(".env", "LICHESS_TOKEN")
        self.session = TokenSession(self.token)
        self.client = Client(self.session)

    def _setup_routes(self):
        # Pages
        self.app.add_url_rule("/", "index", self.index)
        self.app.add_url_rule("/analysis", "analysis", self.analysis)
        self.app.add_url_rule("/blind_puzzles", "blind_puzzles", self.blind_puzzles)
        self.app.add_url_rule("/openings", "openings", self.openings)

        # API
        self.app.add_url_rule("/api/board/<string:board_id>", "api_get_board", self.api_get_board, methods=["GET"])
        self.app.add_url_rule("/api/move/<string:board_id>", "api_move", self.api_move, methods=["POST"])
        self.app.add_url_rule("/api/goto/<string:board_id>/<int:ply>", "api_goto", self.api_goto, methods=["GET"])
        self.app.add_url_rule("/api/undo/<string:board_id>", "api_undo", self.api_undo, methods=["POST"])
        self.app.add_url_rule("/api/reset/<string:board_id>", "api_reset", self.api_reset, methods=["POST"])
        self.app.add_url_rule("/api/fen/<string:board_id>", "api_set_fen", self.api_set_fen, methods=["POST"])

    # ---------- Pages ----------------------------------------------------------

    def index(self):
        """Menu / landing page."""
        return render_template("index.html")

    def analysis(self):
        """Analysis board page."""
        return render_template("analysis.html")

    def blind_puzzles(self):
        """Blindfold puzzles page."""
        return render_template("blind_puzzles.html")

    def openings(self):
        """Openings trainer page."""
        return render_template("openings.html")

    # ---------- API ------------------------------------------------------------

    def _get_board(self, board_id: str = "default") -> ChessBoard:
        if board_id not in self._boards:
            self._boards[board_id] = board_mapping.get(board_id, ChessBoard)(
                client=self.client,
                token=self.token
            )
        return self._boards[board_id]

    def api_get_board(self, board_id: str):
        board = self._get_board(board_id)
        return jsonify(
            fen=board.fen,
            turn=board.turn,
            legal_moves=board.legal_moves(),
            is_check=board.is_check,
            is_game_over=board.is_game_over,
            result=board.result,
            moves=board.move_history,
        )

    def api_move(self, board_id: str):
        data = request.get_json(force=True)
        uci = data.get("uci", "")
        from_ply = data.get("from_ply", None)
        board = self._get_board(board_id)

        is_legal, is_correct = board.push_move_at_ply(uci, from_ply)
        ply = board.total_plies
        return jsonify(
            is_legal=is_legal,
            is_correct=is_correct,
            fen=board.fen,
            turn=board.turn,
            legal_moves=board.legal_moves(),
            is_check=board.is_check,
            is_game_over=board.is_game_over,
            result=board.result,
            moves=board.move_history,
            ply=ply,
        )

    def api_goto(self, board_id: str, ply: int):
        board = self._get_board(board_id)
        pos = board.position_at_ply(ply)
        pos["moves"] = board.move_history
        pos["total_plies"] = board.total_plies
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
        )

    def api_set_fen(self, board_id: str):
        data = request.get_json(force=True)
        fen = data.get("fen", "")
        board = self._get_board(board_id)
        try:
            board.set_fen(fen)
        except ValueError:
            return jsonify(ok=False, error="Invalid FEN"), 400
        return jsonify(
            ok=True,
            fen=board.fen,
            turn=board.turn,
            legal_moves=board.legal_moves(),
            is_check=board.is_check,
            is_game_over=board.is_game_over,
            result=board.result,
            moves=board.move_history,
        )

    # ---------- Entry point ------------------------------------------------------------

    def run(self):
        self.app.run(debug=True)
