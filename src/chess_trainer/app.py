"""Flask application for Chess Trainer."""

from flask import Flask, jsonify, render_template, request

from .boards import ChessBoard

app = Flask(__name__)

# ---------- Pages ----------------------------------------------------------


@app.route("/")
def index():
    """Menu / landing page."""
    return render_template("index.html")


@app.route("/analysis")
def analysis():
    """Analysis board page."""
    return render_template("analysis.html")

@app.route("/blind_puzzles")
def blind_puzzles():
    """Blindfold puzzles page."""
    return render_template("blind_puzzles.html")

@app.route("/openings")
def openings():
    """Openings trainer page."""
    return render_template("openings.html")


# ---------- API ------------------------------------------------------------

_boards: dict[str, ChessBoard] = {}


def _get_board(board_id: str = "default") -> ChessBoard:
    if board_id not in _boards:
        _boards[board_id] = ChessBoard()
    return _boards[board_id]


@app.route("/api/board/<string:board_id>", methods=["GET"])
def api_get_board(board_id: str):
    board = _get_board(board_id)
    return jsonify(
        fen=board.fen,
        turn=board.turn,
        legal_moves=board.legal_moves(),
        is_check=board.is_check,
        is_game_over=board.is_game_over,
        result=board.result,
        moves=board.move_history,
    )


@app.route("/api/move/<string:board_id>", methods=["POST"])
def api_move(board_id: str):
    data = request.get_json(force=True)
    uci = data.get("uci", "")
    from_ply = data.get("from_ply", None)
    board = _get_board(board_id)
    if from_ply is not None:
        board.truncate_to_ply(int(from_ply))
    is_legal, is_correct = board.push_move(uci)
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


@app.route("/api/goto/<string:board_id>/<int:ply>", methods=["GET"])
def api_goto(board_id: str, ply: int):
    board = _get_board(board_id)
    pos = board.position_at_ply(ply)
    pos["moves"] = board.move_history
    pos["total_plies"] = board.total_plies
    return jsonify(**pos)


@app.route("/api/undo/<string:board_id>", methods=["POST"])
def api_undo(board_id: str):
    board = _get_board(board_id)
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


@app.route("/api/reset/<string:board_id>", methods=["POST"])
def api_reset(board_id: str):
    board = _get_board(board_id)
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


@app.route("/api/fen/<string:board_id>", methods=["POST"])
def api_set_fen(board_id: str):
    data = request.get_json(force=True)
    fen = data.get("fen", "")
    board = _get_board(board_id)
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
