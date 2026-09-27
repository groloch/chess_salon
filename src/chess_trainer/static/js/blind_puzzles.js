import { initBoard } from '/static/js/board.js';
import { sendGoto } from '/static/js/api.js';

let sessionId = new URLSearchParams(window.location.search).get('session_id');
const sessionStatus = document.getElementById('timed-session-status');
const sessionCountdown = document.getElementById('session-countdown');
const sessionProgress = document.getElementById('session-progress');
const sessionOverDialog = document.getElementById('session-over-dialog');
const sessionOverSummary = document.getElementById('session-over-summary');
const continueTrainingButton = document.getElementById('continue-training-button');
const finishTrainingButton = document.getElementById('finish-training-button');
let sessionTimer;
let sessionExpired = false;
let timeUp = false;
let groundRef;

function formatRemaining(seconds) {
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function lockBoard() {
    if (groundRef) groundRef.set({ viewOnly: true, drawable: { enabled: false, shapes: [] } });
}

function unlockBoard() {
    if (groundRef) groundRef.set({ viewOnly: false, drawable: { enabled: true } });
}

function expireSession() {
    const firstExpiration = !sessionExpired;
    sessionExpired = true;
    clearInterval(sessionTimer);
    lockBoard();
    if (firstExpiration && sessionProgress) {
        sessionProgress.textContent = 'Session complete — the active puzzle expired and was not counted as a failure.';
    }
}

function showSessionOver(session) {
    if (timeUp) return;
    timeUp = true;
    clearInterval(sessionTimer);
    if (sessionCountdown) sessionCountdown.textContent = '00:00';
    if (sessionProgress) sessionProgress.textContent = 'Time is up — choose how to continue.';
    if (sessionStatus) sessionStatus.classList.add('time-low');
    lockBoard();
    if (sessionOverSummary && session) {
        sessionOverSummary.textContent =
            `${session.completed_count} completed · ${session.successes} solved · ${session.failures} missed.`;
    }
    if (sessionOverDialog) sessionOverDialog.showModal();
}

async function updateSession() {
    if (!sessionId || timeUp || sessionExpired) return;
    try {
        const response = await fetch(`/api/puzzles/session/${encodeURIComponent(sessionId)}`);
        if (!response.ok) throw new Error('Session ended');
        const { session } = await response.json();
        const remaining = Math.max(0, session.remaining_seconds ?? Math.ceil((Date.parse(session.ends_at) - Date.now()) / 1000));
        if (sessionCountdown) sessionCountdown.textContent = formatRemaining(remaining);
        if (sessionProgress) sessionProgress.textContent =
            `${session.completed_count} completed · ${session.successes} solved · ${session.failures} missed`;
        if (sessionStatus) sessionStatus.classList.toggle('time-low', remaining <= 60);
        if (session.status === 'active' && remaining <= 0) {
            showSessionOver(session);
            return;
        }
        if (session.status !== 'active') {
            expireSession();
            if (sessionProgress) {
                sessionProgress.textContent = session.expired_count > 0
                    ? 'Time is up — the active puzzle expired and was not counted as a failure.'
                    : `${session.completed_count} puzzles completed · ${session.successes} solved · ${session.failures} missed.`;
            }
        }
    } catch {
        if (sessionExpired) return;
        expireSession();
    }
}

async function finishTraining() {
    const endingId = sessionId;
    sessionId = null;
    timeUp = false;
    clearInterval(sessionTimer);
    let summary = 'Time is up — the active puzzle expired and was not counted as a failure.';
    try {
        const response = await fetch(`/api/puzzles/session/${encodeURIComponent(endingId)}/finish`, { method: 'POST' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Session could not be ended');
        if (result.session.expired_count === 0) {
            summary = `${result.session.completed_count} puzzles completed · ${result.session.successes} solved · ${result.session.failures} missed.`;
        }
    } catch {
        // Even if the request fails, stop the interactive session locally.
    }
    if (sessionOverDialog) sessionOverDialog.close();
    expireSession();
    if (sessionCountdown) sessionCountdown.textContent = '00:00';
    if (sessionProgress) sessionProgress.textContent = summary;
}

async function continueTraining() {
    const endingId = sessionId;
    let detached = false;
    try {
        const response = await fetch(`/api/puzzles/session/${encodeURIComponent(endingId)}/detach`, { method: 'POST' });
        detached = response.ok;
    } catch {
        detached = false;
    }
    if (!detached) {
        await finishTraining();
        return;
    }
    // Fall back to a normal, untimed training flow: the board keeps fetching
    // puzzles and records every attempt outside the timed session.
    sessionId = null;
    timeUp = false;
    sessionExpired = false;
    clearInterval(sessionTimer);
    if (sessionOverDialog) sessionOverDialog.close();
    if (sessionStatus) sessionStatus.remove();
    unlockBoard();
}

if (sessionId) {
    if (sessionStatus) sessionStatus.hidden = false;
    updateSession();
    sessionTimer = setInterval(updateSession, 1000);
    window.addEventListener('puzzle-session-expired', () => {
        if (!timeUp) updateSession();
    });
    if (sessionOverDialog) {
        sessionOverDialog.addEventListener('cancel', (event) => event.preventDefault());
    }
    if (continueTrainingButton) continueTrainingButton.addEventListener('click', continueTraining);
    if (finishTrainingButton) finishTrainingButton.addEventListener('click', finishTraining);
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
            if (arrow && !sessionExpired && !timeUp) {
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
