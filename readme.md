# Chess Trainer

A local web app for blindfold puzzle training.

## Features

- **Analysis board** (`/analysis`)
- **Blindfold puzzles** (`/blind_puzzles`, `/blind_puzzles/play`): fetch Lichess puzzles, hide the last N plies of the game, and play the solution from the resulting position. Puzzles are sampled from a configurable mix of difficulties and blindfold depths.

## Requirements

- Python 3.12
- Node.js 18+
- A Lichess API token with puzzle:read and puzzle:write permissions.

## Setup

On linux : (for windows user the env activation is different)
```bash
python3 -m venv .venv_chsl
source .venv_chsl/bin/activate
pip install -r requirements.txt
```

Add your lichess token to `.env`:

```dotenv
LICHESS_TOKEN=your_token_here
```

You lichess token needs to have the following rights:
- Read puzzle activity: `puzzle:read`
- Solve puzzles: `puzzle:write`

## Run

```bash
PYTHONPATH=src python3 -m chess_trainer
```

The server listens on port `23777` with Flask debug mode enabled.

## Frontend build

```bash
cd src/chess_trainer/static
npm ci
npm run build
```

This bundles `js/main.js` into `dist/bundle.js` and copies the Chessground stylesheets.

## Acknowledgements

- [Chessground](https://github.com/lichess-org/chessground) — board UI, GPL-3.0 licensed.
- [python-chess](https://github.com/niklasf/python-chess) — board state and move generation, GPL-3.0 licensed.
- [Lichess](https://lichess.org) — puzzle data and solving API.
- [Berserk](https://github.com/lichess-org/berserk) — Lichess API client, GPL-3.0 licensed.
- [Flask](https://flask.palletsprojects.com) and [esbuild](https://esbuild.github.io) — web framework and frontend bundler.

