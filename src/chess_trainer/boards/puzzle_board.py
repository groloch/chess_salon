from . import ChessBoard

from dataclasses import dataclass
from typing import Literal
import requests

import chess
from berserk import Client


@dataclass
class BlindfoldPuzzleConfig:
    mode: Literal['easiest', 'easier', 'normal', 'harder', 'hardest'] = 'easiest'
    blindfold_depth: int = 9


class BlindfoldPuzzleChessBoard(ChessBoard):
    def __init__(
            self,
            client : Client,
            token: str,
            config: BlindfoldPuzzleConfig = BlindfoldPuzzleConfig(),
            history_store=None,
            session_id: str | None = None,
            **kwargs):
        super().__init__()

        self.client: Client = client
        self.config = config
        self.history_store = history_store
        self.session_id = session_id

        self.solution, self.rating, self.current_puzzle_id = self.fetch_next_puzzle()
        self.current_puzzle_ply = 0
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }

    def fetch_next_puzzle(self):
        if self.history_store is not None and self.session_id is not None:
            session = self.history_store.get_session(self.session_id)
            if session is None or session['status'] != 'active' or session['remaining_seconds'] <= 0:
                raise RuntimeError('The timed puzzle session has ended.')

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

        # Preserve the side-to-move perspective of the puzzle position even
        # while the UI navigates back through the preceding blindfold plies.
        self.orientation = 'white' if self.board.turn == chess.WHITE else 'black'
        self.puzzle_start_fen = self.board.fen()
        self.puzzle_start_moves = self.move_history
        self.current_puzzle_ply = 0
        if self.history_store is not None and self.session_id is not None:
            saved = self.history_store.set_session_puzzle(
                self.session_id,
                puzzle_id=puzzle['puzzle']['id'],
                rating=rating,
                fen=self.puzzle_start_fen,
                orientation=self.orientation,
                moves=self.puzzle_start_moves,
            )
            if not saved:
                raise RuntimeError('The timed puzzle session expired while fetching a puzzle.')

        return solution, rating, puzzle['puzzle']['id']

    def _record_attempt(self, success: bool) -> bool:
        if self.history_store is None:
            return True
        return self.history_store.record_attempt(
            puzzle_id=self.current_puzzle_id,
            rating=self.rating,
            success=success,
            fen=self.puzzle_start_fen,
            orientation=self.orientation,
            moves=self.puzzle_start_moves,
            session_id=self.session_id,
        )

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
                    recorded = self._record_attempt(success=True)
                    if not recorded and self.session_id is not None:
                        self.history_store.expire_session_if_due(self.session_id)
                        return {
                            'legal': legal, 'correct': correct, 'completed': False,
                            'win': False, 'session_expired': True,
                        }
                    self.notify_puzzle_completed(puzzle_id=self.current_puzzle_id, win=True)
                    completed = True
                    win = True
                    try:
                        self.solution, self.rating, self.current_puzzle_id = self.fetch_next_puzzle()
                    except RuntimeError:
                        if self.session_id is None:
                            raise
                        return {
                            'legal': legal, 'correct': correct, 'completed': completed,
                            'win': win, 'session_expired': True,
                        }
                else:
                    for _ in range(2):
                        self.board.push(chess.Move.from_uci(self.solution[self.current_puzzle_ply]))
                        self.current_puzzle_ply += 1
            else:
                recorded = self._record_attempt(success=False)
                if not recorded and self.session_id is not None:
                    self.history_store.expire_session_if_due(self.session_id)
                    return {
                        'legal': legal, 'correct': correct, 'completed': False,
                        'win': False, 'session_expired': True,
                    }
                self.notify_puzzle_completed(puzzle_id=self.current_puzzle_id, win=False)
                completed = True
                win = False
                try:
                    self.solution, self.rating, self.current_puzzle_id = self.fetch_next_puzzle()
                except RuntimeError:
                    if self.session_id is None:
                        raise
                    return {
                        'legal': legal, 'correct': correct, 'completed': completed,
                        'win': win, 'session_expired': True,
                    }

        return {'legal': legal, 'correct': correct, 'completed': completed, 'win': win}

    def push_move_at_ply(self, uci_move, from_ply = None):
        return self.push_move(uci_move)
