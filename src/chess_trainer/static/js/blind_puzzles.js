import { initBoard } from '/static/js/board.js';
import { sendGoto } from '/static/js/api.js';

initBoard({
    enableKeyboard: false,
    enableScroll: false,
    boardId: 'puzzles',
    animation: { enabled: false }
    }).then(({ syncBoard, attemptMove, ground }) => {
    ground.set({
        drawable: {
        enabled: true,
        onChange: (shapes) => {
            const arrow = shapes.find(s => s.orig && s.dest);
            if (arrow) {
            attemptMove(arrow.orig, arrow.dest, null, true).then(() => {
                ground.set({ drawable: { shapes: [] } });
                sendGoto('puzzles', 0).then(syncBoard);
            });
            }
        }
        }
    });

    sendGoto('puzzles', 0).then(syncBoard);
});
