/**
 * Chess Trainer – API helpers
 *
 * Pure fetch wrappers for the Flask back-end.
 * Every function returns the parsed JSON response.
 */

function uci(orig, dest, prom) {
  return orig + dest + (prom || '');
}

export async function fetchBoard(boardId) {
  const r = await fetch(`/api/board/${boardId}`);
  return r.json();
}

export async function sendMove(boardId, orig, dest, prom, fromPly) {
  const r = await fetch(`/api/move/${boardId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uci: uci(orig, dest, prom), from_ply: fromPly }),
  });
  return r.json();
}

export async function sendGoto(boardId, ply) {
  const r = await fetch(`/api/goto/${boardId}/${ply}`);
  return r.json();
}

export async function sendReset(boardId) {
  const r = await fetch(`/api/reset/${boardId}`, { method: 'POST' });
  return r.json();
}

export async function sendFen(boardId, fen) {
  const r = await fetch(`/api/fen/${boardId}`, {

    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fen }),
  });
  return r.json();
}