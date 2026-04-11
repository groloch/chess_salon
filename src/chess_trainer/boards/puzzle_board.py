from . import ChessBoard

from dataclasses import dataclass
from typing import Literal

import chess
from berserk import Client


@dataclass
class BlindfoldPuzzleConfig:
    mode: Literal['easiest', 'easier', 'normal', 'harder', 'hardest'] = 'easiest'
    blindfold_depth: int = 3


class BlindfoldPuzzleChessBoard(ChessBoard):
    def __init__(self, client : Client, config: BlindfoldPuzzleConfig = BlindfoldPuzzleConfig(), **kwargs):
        super().__init__()

        self.client: Client = client
        self.config = config

        self.solution, self.rating = self.fetch_next_puzzle()
        self.current_puzzle_ply = 0

    def fetch_next_puzzle(self):
        puzzle = self.client.puzzles.get_next(difficulty=self.config.mode)

        movelist = puzzle['game']['pgn'].split()
        solution = puzzle['puzzle']['solution']
        rating = puzzle['puzzle']['rating']

        board = chess.Board()

        for move in movelist[:-self.config.blindfold_depth]:
            board.push_san(move)

        blind_moves = movelist[-self.config.blindfold_depth:]
        fen = board.fen()
        self.board = chess.Board(fen)
        self.start_fen = fen

        for move in blind_moves:
            self.board.push_san(move)

        return solution, rating

    def is_correct(self, uci_move: str) -> bool:
        return uci_move == self.solution[self.current_puzzle_ply]

    def push_move(self, uci_move: str) -> tuple[bool, bool]:
        """Attempts to play a move given in UCI notation.
        Returns a tuple (is_legal, is_correct).
        """
        if not self.is_legal(uci_move):
            return False, True
        if not self.is_correct(uci_move):
            return True, False

        if self.current_puzzle_ply >= len(self.solution) - 2:
            self.fetch_next_puzzle()
            return True, True

        else:
            for _ in range(2):
                self.board.push(chess.Move.from_uci(uci_move))
                self.current_puzzle_ply += 1
            return True, True

    def push_move_at_ply(self, uci_move, from_ply = None):
        return self.push_move(uci_move)