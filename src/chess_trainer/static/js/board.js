/**
 * Chess Trainer – reusable board controller
 *
 * Call `initBoard(options)` from any page to wire up a Chessground board,
 * move list, keyboard / scroll navigation, flip button, FEN input, etc.
 *
 * Options (all optional):
 *   boardEl        – DOM element for the board           (default: #board)
 *   movelistEl     – DOM element for the move list       (default: #movelist)
 *   fenInputEl     – DOM element for the FEN input       (default: #fen-input)
 *   btnFlipEl      – DOM element for the flip button     (default: #btn-flip)
 *   btnResetEl     – DOM element for the reset button    (default: #btn-reset, may be null)
 *   btnFenEl       – DOM element for the load-FEN button (default: #btn-fen)
 *   orientation    – initial orientation ('white'|'black')
 *   enableKeyboard – bind arrow-key navigation           (default: true)
 *   enableScroll   – bind mouse-wheel navigation         (default: true)
 */

import { Chessground } from '/static/dist/bundle.js';
import {
  fetchBoard,
  sendMove,
  sendGoto,
  sendReset,
  sendFen,
} from '/static/js/api.js';

// ── utility ──────────────────────────────────────────────────────

function destsFromLegal(legalMoves) {
  const dests = new Map();
  for (const m of legalMoves) {
    const orig = m.slice(0, 2);
    const dest = m.slice(2, 4);
    if (!dests.has(orig)) dests.set(orig, []);
    dests.get(orig).push(dest);
  }
  return dests;
}

function showPromotionDialog(boardEl, dest, color, orientation, callback) {
  const fileCharCode = dest.charCodeAt(0) - 97;
  const rank = parseInt(dest[1], 10);
  
  const vFile = orientation === 'white' ? fileCharCode : 7 - fileCharCode;
  const vRank = orientation === 'white' ? 8 - rank : rank - 1;

  const overlay = document.createElement('div');
  overlay.style.position = 'absolute';
  overlay.style.top = '0';
  overlay.style.left = '0';
  overlay.style.width = '100%';
  overlay.style.height = '100%';
  overlay.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';
  overlay.style.zIndex = '200';
  
  const container = document.createElement('div');
  container.style.position = 'absolute';
  container.style.left = `${vFile * 12.5}%`;
  container.style.width = '12.5%';
  container.style.height = '50%';
  container.style.display = 'flex';
  
  if (vRank <= 3) {
    container.style.top = `${vRank * 12.5}%`;
    container.style.flexDirection = 'column';
  } else {
    container.style.bottom = `${(7 - vRank) * 12.5}%`;
    container.style.flexDirection = 'column-reverse';
  }

  container.addEventListener('click', (e) => e.stopPropagation());

  const cancel = () => {
    callback(null);
    overlay.remove();
    delete boardEl.cancelPromotion;
  };
  boardEl.cancelPromotion = cancel;

  overlay.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    cancel();
  });
  overlay.addEventListener('click', (e) => {
    e.stopPropagation();
    cancel();
  });

  const pieces = ['q', 'r', 'b', 'n'];
  const pieceNames = { 'q': 'queen', 'r': 'rook', 'b': 'bishop', 'n': 'knight' };

  for (const p of pieces) {
    const pieceEl = document.createElement('piece');
    pieceEl.className = `${pieceNames[p]} ${color}`;
    pieceEl.style.width = '100%';
    pieceEl.style.height = '25%';
    pieceEl.style.position = 'relative'; /* Prevent stacking */
    pieceEl.style.pointerEvents = 'auto'; /* Allow clicking regardless of parent */
    pieceEl.style.cursor = 'pointer';
    pieceEl.style.backgroundSize = 'contain';
    pieceEl.style.backgroundRepeat = 'no-repeat';
    pieceEl.style.backgroundPosition = 'center';
    
    // Add light gray circle background
    pieceEl.style.backgroundColor = '#d3d3d3';
    pieceEl.style.borderRadius = '50%';
    pieceEl.style.boxShadow = '0 2px 4px rgba(0,0,0,0.5)';
    
    pieceEl.addEventListener('mouseenter', () => {
      pieceEl.style.backgroundColor = '#e8e8e8'; // slightly lighter on hover
    });
    pieceEl.addEventListener('mouseleave', () => {
      pieceEl.style.backgroundColor = '#d3d3d3';
    });

    pieceEl.addEventListener('click', (e) => {
      e.stopPropagation();
      callback(p);
      overlay.remove();
      delete boardEl.cancelPromotion;
    });

    container.appendChild(pieceEl);
  }

  overlay.appendChild(container);
  boardEl.appendChild(overlay);
}

// ── public entry-point ───────────────────────────────────────────

export async function initBoard(opts = {}) {
  const boardId = opts.boardId || 'default';
  const boardEl    = opts.boardEl    ?? document.getElementById('board');
  const movelistEl = opts.movelistEl ?? document.getElementById('movelist');
  const fenInputEl = opts.fenInputEl ?? document.getElementById('fen-input');
  const btnFlipEl  = opts.btnFlipEl  ?? document.getElementById('btn-flip');
  const btnResetEl = opts.btnResetEl ?? document.getElementById('btn-reset');
  const btnFenEl   = opts.btnFenEl   ?? document.getElementById('btn-fen');

  let orientation = opts.orientation ?? 'white';
  let currentPly  = 0;
  let totalPlies  = 0;
  let ground;
  let currentLegalMoves = [];
  let currentTurnColor = 'white';

  // ── move list rendering ──────────────────────────────────────

  function renderMoveList(moves) {
    movelistEl.innerHTML = '';
    let row = null;
    for (const m of moves) {
      const isWhite = m.ply % 2 === 0;
      if (isWhite) {
        row = document.createElement('div');
        row.className = 'move-row';
        const num = document.createElement('span');
        num.className = 'move-number';
        num.textContent = `${Math.floor(m.ply / 2) + 1}.`;
        row.appendChild(num);
        movelistEl.appendChild(row);
      }
      const span = document.createElement('span');
      span.className = 'move';
      span.textContent = m.san;
      if (m.ply === currentPly - 1) span.classList.add('current');
      if (row) row.appendChild(span);
    }
    movelistEl.scrollTop = movelistEl.scrollHeight;
  }

  // ── sync board UI ────────────────────────────────────────────

  function syncBoard(data) {
    if (data.ply !== undefined) currentPly = data.ply;
    if (data.total_plies !== undefined) totalPlies = data.total_plies;
    else totalPlies = (data.moves || []).length;

    currentLegalMoves = data.legal_moves || [];
    currentTurnColor = data.turn;
    const dests = destsFromLegal(currentLegalMoves);
    ground.set({
      fen: data.fen,
      turnColor: data.turn,
      orientation,
      movable: {
        color: data.turn,
        free: false,
        dests,
      },
      check: data.is_check ? data.turn : false,
    });
    if (fenInputEl) fenInputEl.value = data.fen;
    renderMoveList(data.moves || []);
  }

  function navigatePly(boardId, delta) {
    if (boardEl.cancelPromotion) {
      boardEl.cancelPromotion();
      return;
    }
    const target = currentPly + delta;
    if (target < 0 || target > totalPlies) return;
    sendGoto(boardId, target).then(syncBoard);
  }

  function attemptMove(orig, dest, prom) {
    return sendMove(boardId, orig, dest, prom, currentPly)
      .then(data => {
        if(data.is_legal === false){
          // TODO handle illegal move
        }else if(data.is_correct === false){
          // TODO handle incorrect move
        }
        return data;
      })
      .then(syncBoard)
      .catch((err) => {
        console.error(err);
        sendGoto(boardId, currentPly).then(syncBoard);
      }
    );
  }

  // ── initialise chessground ───────────────────────────────────

  const data = await fetchBoard(boardId);
  currentLegalMoves = data.legal_moves || [];
  currentTurnColor = data.turn;
  const dests = destsFromLegal(currentLegalMoves);

  currentPly  = (data.moves || []).length;
  totalPlies  = currentPly;

  ground = Chessground(boardEl, {
    fen: data.fen,
    orientation,
    turnColor: data.turn,
    movable: {
      color: data.turn,
      free: false,
      dests,
      events: {
        after(orig, dest) {
          const moveUci = orig + dest;
          const isPromotion = currentLegalMoves.some(m => m.startsWith(moveUci) && m.length > 4);
          
          if (isPromotion) {
            showPromotionDialog(boardEl, dest, currentTurnColor, orientation, (prom) => {
              if (!prom) {
                // Cancelled
                sendGoto(boardId, currentPly).then(syncBoard);
              } else {
                attemptMove(orig, dest, prom);
              }
            });
          } else {
            attemptMove(orig, dest, null)
          }
        },
      },
    },
    draggable: { showGhost: true },
  });

  if (fenInputEl) fenInputEl.value = data.fen;
  renderMoveList(data.moves || []);

  // ── button wiring ────────────────────────────────────────────

  if (btnFlipEl) {
    btnFlipEl.addEventListener('click', () => {
      orientation = orientation === 'white' ? 'black' : 'white';
      ground.set({ orientation });
    });
  }

  if (btnResetEl) {
    btnResetEl.addEventListener('click', () => {
      sendReset(boardId).then(syncBoard);
    });
  }

  if (btnFenEl && fenInputEl) {
    btnFenEl.addEventListener('click', () => {
      const fen = fenInputEl.value.trim();
      if (fen) sendFen(boardId, fen).then(syncBoard);
    });
  }

  // ── keyboard navigation ──────────────────────────────────────

  if (opts.enableKeyboard !== false) {
    window.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight') { e.preventDefault(); navigatePly(boardId, 1);  }
      else if (e.key === 'ArrowLeft')  { e.preventDefault(); navigatePly(boardId, -1); }
    });
  }

  // ── scroll navigation ────────────────────────────────────────

  if (opts.enableScroll !== false) {
    boardEl.addEventListener('wheel', (e) => {
      e.preventDefault();
      if (Math.abs(e.deltaY) < 4) return;
      navigatePly(boardId, e.deltaY > 0 ? 1 : -1);
    }, { passive: false });
  }

  // Return a handle so pages can extend behaviour if needed.
  return { ground, syncBoard, navigatePly };
}
