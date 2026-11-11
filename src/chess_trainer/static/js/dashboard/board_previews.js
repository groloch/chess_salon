/**
 * Initialize Chessground previews from template-rendered placeholders
 * (.mini-board[data-fen]). Without JS the card text remains readable.
 */
import { Chessground } from '/static/dist/bundle.js';

export function initBoardPreviews(root = document) {
  for (const element of root.querySelectorAll('.mini-board[data-fen]')) {
    if (element.dataset.previewReady) continue;
    element.dataset.previewReady = '1';
    Chessground(element, {
      fen: element.dataset.fen,
      orientation: element.dataset.orientation === 'black' ? 'black' : 'white',
      coordinates: false,
      viewOnly: true,
      animation: { enabled: false },
    });
  }
}
