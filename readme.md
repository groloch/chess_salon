# Chess Trainer

A local web app for blindfold puzzle training.

## Features

- **Analysis board** (`/analysis`)
- **Blindfold puzzles** (`/blind_puzzles`, `/blind_puzzles/play`): fetch Lichess puzzles, hide the last N plies of the game, and play the solution from the resulting position. Puzzles are sampled from a configurable mix of difficulties and blindfold depths.
- **Sign in with Lichess**: each user links their own Lichess account via OAuth2 (Authorization Code + PKCE). Puzzle training and history are per-user; the analysis board works without signing in.

## Requirements

- Python 3.12+
- Node.js 18+
- A Lichess OAuth app (see below) with the `puzzle:read` and `puzzle:write` scopes.

## Setup

On linux : (for windows user the env activation is different)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Choose a unique client id for this app and add it to `.env`. Lichess supports
public OAuth clients without registration. The default redirect URI is
`http://localhost:23777/auth/callback`, with scopes `puzzle:read puzzle:write`
(read puzzle activity + solve puzzles).

```dotenv
LICHESS_CLIENT_ID=your_client_id_here
# LICHESS_REDIRECT_URI=http://localhost:23777/auth/callback
# LICHESS_SCOPES=puzzle:read puzzle:write
```

`SECRET_KEY` (session signing) and `TOKEN_ENCRYPTION_KEY` (encrypting Lichess
access tokens at rest) are generated automatically into `.env` on first run.
`LICHESS_TOKEN` is no longer used by the web app; it is only read by the
standalone board CLI (`python -m chess_trainer.boards`).

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

