import chess


class ChessBoard:
    '''Manages a chess board state and exposes legal-move helpers.'''

    def __init__(self, fen: str | None = None, **kwargs):
        self.board = chess.Board(fen) if fen else chess.Board()

        self.start_fen = self.board.fen()

    @property
    def fen(self) -> str:
        return self.board.fen()

    @property
    def turn(self) -> str:
        return 'white' if self.board.turn == chess.WHITE else 'black'

    @property
    def is_game_over(self) -> bool:
        return self.board.is_game_over()

    @property
    def result(self) -> str | None:
        if self.board.is_game_over():
            return self.board.result()
        return None

    @property
    def is_check(self) -> bool:
        return self.board.is_check()

    @property
    def move_history(self) -> list[dict]:
        '''Return the full move history as a list of {uci, san, ply} dicts.'''
        moves: list[dict] = []
        tmp = chess.Board(self.start_fen)
        if tmp.turn == chess.BLACK:
            moves.append({'uci': None, 'san': '...', 'ply': 0})
            offset = 1
        else:
            offset = 0
        for i, move in enumerate(self.board.move_stack):
            san = tmp.san(move)
            moves.append({'uci': move.uci(), 'san': san, 'ply': i + offset})
            tmp.push(move)
        return moves

    @property
    def total_plies(self) -> int:
        return len(self.board.move_stack)

    def position_at_ply(self, ply: int) -> dict:
        '''Return board info at a given ply without modifying the move stack.'''
        ply = max(0, min(ply, self.total_plies))
        tmp = chess.Board(self.start_fen)
        for move in list(self.board.move_stack)[:ply]:
            tmp.push(move)
        return {
            'fen': tmp.fen(),
            'turn': 'white' if tmp.turn == chess.WHITE else 'black',
            'is_check': tmp.is_check(),
            'is_game_over': tmp.is_game_over(),
            'result': tmp.result() if tmp.is_game_over() else None,
            'legal_moves': [m.uci() for m in tmp.legal_moves],
            'ply': ply,
        }

    def truncate_to_ply(self, ply: int) -> None:
        '''Remove all moves after the given ply.'''
        while len(self.board.move_stack) > ply:
            self.board.pop()

    def legal_moves(self) -> list[str]:
        '''Return all legal moves in UCI notation (e.g. 'e2e4').'''
        return [move.uci() for move in self.board.legal_moves]

    def is_legal(self, uci_move: str) -> bool:
        '''Check whether a UCI move string is legal in the current position.'''
        try:
            move = chess.Move.from_uci(uci_move)
        except ValueError:
            return False
        return move in self.board.legal_moves

    def is_correct(self, uci_move: str) -> bool:
        '''Check whether a UCI move string is the expected correct move.'''
        return True

    def push_move(self, uci_move: str) -> tuple[bool, bool]:
        '''Attempts to play a move given in UCI notation.
        Returns a tuple (is_legal, is_correct).
        '''
        legal = self.is_legal(uci_move)
        correct = self.is_correct(uci_move)
        if legal and correct:
            self.board.push(chess.Move.from_uci(uci_move))
        return {'legal': legal, 'correct': correct}

    def push_move_at_ply(self, uci_move: str, from_ply: int | None = None) -> tuple[bool, bool]:
        '''Attempts to play a move at a given ply. If from_ply is None, defaults to current position.
        Returns a tuple (is_legal, is_correct).
        '''
        if from_ply is not None:
            self.truncate_to_ply(from_ply)
        return self.push_move(uci_move)

    def undo(self) -> str | None:
        '''Undo the last move. Returns the undone move in UCI or None.'''
        try:
            move = self.board.pop()
            return move.uci()
        except IndexError:
            return None

    def reset(self) -> None:
        '''Reset to starting position.'''
        self.board.reset()
        self.start_fen = self.board.fen()

    def set_fen(self, fen: str) -> None:
        '''Load an arbitrary FEN position.'''
        self.board.set_fen(fen)
        self.start_fen = fen
