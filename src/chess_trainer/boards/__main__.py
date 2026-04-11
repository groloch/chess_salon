from . import BlindfoldPuzzleChessBoard, BlindfoldPuzzleConfig

def test_blindfold_puzzle_chess_board():
    import dotenv
    import berserk

    dotenv.load_dotenv()
    token = dotenv.get_key(dotenv.find_dotenv(), "LICHESS_TOKEN")
    
    session = berserk.TokenSession(token)
    client = berserk.Client(session=session)
    
    config = BlindfoldPuzzleConfig(mode='easiest')
    board = BlindfoldPuzzleChessBoard(client=client, config=config)


if __name__ == "__main__":
    test_blindfold_puzzle_chess_board()