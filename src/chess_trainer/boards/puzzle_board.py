from . import ChessBoard

from dataclasses import dataclass
from typing import Literal
import requests

import chess
from berserk import Client


@dataclass
class BlindfoldPuzzleConfig:
    mode: Literal['easiest', 'easier', 'normal', 'harder', 'hardest'] = 'easiest'
    blindfold_depth: int = 5


class BlindfoldPuzzleChessBoard(ChessBoard):
    def __init__(
            self,
            client : Client,
            token: str,
            config: BlindfoldPuzzleConfig = BlindfoldPuzzleConfig(),
            **kwargs):
        super().__init__()

        self.client: Client = client
        self.config = config

        self.solution, self.rating, self.current_puzzle_id = self.fetch_next_puzzle()
        self.current_puzzle_ply = 0
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }

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

        self.current_puzzle_ply = 0

        return solution, rating, puzzle['puzzle']['id']

    def is_correct(self, uci_move: str) -> bool:
        return uci_move == self.solution[self.current_puzzle_ply]

    def notify_puzzle_completed(self, puzzle_id, win: bool):
        # This is a workaround until the puzzle solving endpoint is implemented in berserk
        payload = {
            'solutions': [
                {
                    'id': puzzle_id,
                    'win': win,
                    'rated': False
                }
            ]
        }
        response = requests.post(
            f'https://lichess.org/api/puzzle/batch/mix?nb=0',
            json=payload,
            headers=self.headers
        )

        if response.status_code != 200:
            return False
        else:
            return True

    def push_move(self, uci_move: str) -> tuple[bool, bool]:
        '''Attempts to play a move given in UCI notation.
        Returns a tuple (is_legal, is_correct).
        '''
        legal = self.is_legal(uci_move)
        correct = self.is_correct(uci_move)
        completed = False
        win = False

        if legal:
            if correct:
                if self.current_puzzle_ply >= len(self.solution) - 2:
                    self.notify_puzzle_completed(puzzle_id=self.current_puzzle_id, win=True)
                    self.solution, self.rating, self.current_puzzle_id = self.fetch_next_puzzle()
                    completed = True
                    win = True
                else:
                    for _ in range(2):
                        self.board.push(chess.Move.from_uci(self.solution[self.current_puzzle_ply]))
                        self.current_puzzle_ply += 1
            else:
                self.notify_puzzle_completed(puzzle_id=self.current_puzzle_id, win=False)
                self.solution, self.rating, self.current_puzzle_id = self.fetch_next_puzzle()
                completed = True
                win = False

        return {'legal': legal, 'correct': correct, 'completed': completed, 'win': win}

    def push_move_at_ply(self, uci_move, from_ply = None):
        return self.push_move(uci_move)
