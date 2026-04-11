from .basic_board import ChessBoard

from .puzzle_board import BlindfoldPuzzleChessBoard, BlindfoldPuzzleConfig


board_mapping = {
    'default': ChessBoard,
    'analysis': ChessBoard,
    'puzzles': BlindfoldPuzzleChessBoard,
    'openings': ChessBoard
}