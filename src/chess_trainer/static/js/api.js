/**
 * Chess Trainer – API helpers
 *
 * Pure fetch wrappers for the Flask back-end.
 * Every function returns the parsed JSON response.
 */

function uci(orig, dest, prom) {
  return orig + dest + (prom || '');
}

export async function fetchBoard() {
  const r = await fetch('/api/board');
  return r.json();
}

export async function sendMove(orig, dest, prom, fromPly) {
  const r = await fetch('/api/move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uci: uci(orig, dest, prom), from_ply: fromPly }),
  });
  return r.json();
}

export async function sendGoto(ply) {
  const r = await fetch(`/api/goto/${ply}`);
  return r.json();
}

export async function sendReset() {
  const r = await fetch('/api/reset', { method: 'POST' });
  return r.json();
}

export async function sendFen(fen) {
  const r = await fetch('/api/fen', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fen }),
  });
  return r.json();
}

export async function fetchDestsForSquare(square) {
  const r = await fetch(`/api/legal_moves/${square}`);
  const data = await r.json();
  return data.destinations;
}
