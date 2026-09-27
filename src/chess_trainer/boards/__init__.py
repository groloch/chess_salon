from .basic_board import ChessBoard

from .puzzle_board import BlindfoldPuzzleChessBoard, BlindfoldPuzzleConfig
from .puzzle_mix import PuzzleMix


board_mapping = {
    'default': ChessBoard,
    'analysis': ChessBoard,
    'puzzles': BlindfoldPuzzleChessBoard,
    'openings': ChessBoard
}