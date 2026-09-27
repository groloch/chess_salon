import { initBoard } from '/static/js/board.js';
import { sendGoto } from '/static/js/api.js';

const sessionId = new URLSearchParams(window.location.search).get('session_id');
const sessionStatus = document.getElementById('timed-session-status');
const sessionCountdown = document.getElementById('session-countdown');
const sessionProgress = document.getElementById('session-progress');
let sessionTimer;
let sessionExpired = false;
let finishRequestPending = false;
let groundRef;

function formatRemaining(seconds) {
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function expireSession() {
    const firstExpiration = !sessionExpired;
    sessionExpired = true;
    clearInterval(sessionTimer);
    if (groundRef) groundRef.set({ viewOnly: true, drawable: { enabled: false, shapes: [] } });
    if (firstExpiration && sessionProgress) {
        sessionProgress.textContent = 'Session complete — the active puzzle expired and was not counted as a failure.';
    }
}

async function updateSession() {
    if (!sessionId) return;
    try {
        const response = await fetch(`/api/puzzles/session/${encodeURIComponent(sessionId)}`);
        if (!response.ok) throw new Error('Session ended');
        const { session } = await response.json();
        const remaining = Math.max(0, session.remaining_seconds ?? Math.ceil((Date.parse(session.ends_at) - Date.now()) / 1000));
        sessionCountdown.textContent = formatRemaining(remaining);
        sessionProgress.textContent = `${session.completed_count} completed · ${session.successes} solved · ${session.failures} missed`;
        sessionStatus.classList.toggle('time-low', remaining <= 60);
        if (session.status === 'active' && remaining <= 0) {
            sessionProgress.textContent = 'Finishing timed session…';
            if (finishRequestPending) return;
            finishRequestPending = true;
            try {
                const response = await fetch(`/api/puzzles/session/${encodeURIComponent(sessionId)}/finish`, { method: 'POST' });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'Session could not be ended');
                sessionCountdown.textContent = '00:00';
                sessionProgress.textContent = result.session.expired_count > 0
                    ? 'Time is up — the active puzzle expired and was not counted as a failure.'
                    : `${session.completed_count} puzzles completed · ${session.successes} solved · ${session.failures} missed.`;
                expireSession();
            } catch {
                expireSession();
            } finally {
                finishRequestPending = false;
            }
            return;
        }
        if (remaining <= 0 || session.status !== 'active') {
            const status = session.status;
            expireSession();
            if (session.expired_count > 0) {
                sessionProgress.textContent = 'Time is up — the active puzzle expired and was not counted as a failure.';
            } else if (status !== 'active') {
                sessionProgress.textContent = `${session.completed_count} puzzles completed · ${session.successes} solved · ${session.failures} missed.`;
            }
            return;
        }
    } catch {
        if (sessionExpired) return;
        expireSession();
    }
}

if (sessionId) {
    sessionStatus.hidden = false;
    updateSession();
    sessionTimer = setInterval(updateSession, 1000);
    window.addEventListener('puzzle-session-expired', () => {
        expireSession();
        updateSession();
    });
}

initBoard({
    enableKeyboard: false,
    enableScroll: false,
    boardId: 'puzzles',
    useServerOrientation: true,
    animation: { enabled: false }
    }).then(({ syncBoard, attemptMove, ground }) => {
    groundRef = ground;
    if (sessionExpired) expireSession();
    ground.set({
        drawable: {
        enabled: true,
        onChange: (shapes) => {
            const arrow = shapes.find(s => s.orig && s.dest);
            if (arrow && !sessionExpired) {
            // Chessground reports square names (e.g. e7, e5), regardless of
            // orientation, so concatenate them directly as a UCI move.
            attemptMove(arrow.orig, arrow.dest, null, true).then((result) => {
                ground.set({ drawable: { shapes: [] } });
                if (result?.session_expired) {
                    updateSession();
                    return;
                }
                if (result?.completed && sessionId) updateSession();
                // Intermediate solution moves also advance the server board,
                // so always resync or the board never reflects the move.
                sendGoto('puzzles', 0).then((position) => {
                    if (position.session_expired) updateSession();
                    else syncBoard(position);
                });
            });
            }
        }
        }
    });

    sendGoto('puzzles', 0).then((position) => {
        if (position.session_expired) expireSession();
        else syncBoard(position);
    });
});
