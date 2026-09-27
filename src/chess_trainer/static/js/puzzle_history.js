import { Chessground } from '/static/dist/bundle.js';

const statusEl = document.getElementById('history-status');
const contentEl = document.getElementById('history-content');
const daysEl = document.getElementById('history-days');
const statElements = Object.fromEntries(
  [...document.querySelectorAll('[data-stat]')].map(el => [el.dataset.stat, el])
);
const settingsDialog = document.getElementById('puzzle-settings-dialog');
const settingsForm = document.getElementById('puzzle-settings-form');
const startButton = document.getElementById('start-puzzle-submit');
const startError = document.getElementById('puzzle-start-error');
const timedSessionInput = document.getElementById('timed-session');
const durationField = document.querySelector('.duration-setting');

function localDay(isoTimestamp) {
  const date = new Date(isoTimestamp);
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  }).format(date);
}

function localTime(isoTimestamp) {
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric', minute: '2-digit',
  }).format(new Date(isoTimestamp));
}

function displayStats(stats) {
  for (const [key, element] of Object.entries(statElements)) {
    const value = stats[key] ?? 0;
    element.textContent = key === 'success_rate' ? `${value}%` : value;
  }
}

function makePuzzleCard(entry) {
  const link = document.createElement('a');
  link.className = 'puzzle-card';
  link.href = `https://lichess.org/training/${encodeURIComponent(entry.puzzle_id)}`;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.setAttribute('aria-label', `${entry.success ? 'Solved' : 'Failed'} puzzle rated ${entry.rating}, ${localTime(entry.completed_at)}. Open on Lichess.`);

  const boardEl = document.createElement('div');
  boardEl.className = 'mini-board';
  const ground = Chessground(boardEl, {
    fen: entry.fen,
    orientation: entry.orientation,
    coordinates: false,
    viewOnly: true,
    animation: { enabled: false },
  });

  const info = document.createElement('div');
  info.className = 'puzzle-card-info';
  const top = document.createElement('div');
  top.className = 'puzzle-card-top';
  const result = document.createElement('span');
  const outcome = entry.outcome || (entry.success ? 'success' : 'failure');
  result.className = `puzzle-result ${outcome}`;
  result.textContent = outcome === 'success' ? '✓ Solved' : outcome === 'expired' ? '◷ Time' : '✕ Missed';
  const rating = document.createElement('span');
  rating.className = 'puzzle-rating';
  rating.textContent = `♟ ${entry.rating}`;
  top.append(result, rating);
  const time = document.createElement('time');
  time.className = 'puzzle-time';
  time.dateTime = entry.completed_at;
  time.textContent = localTime(entry.completed_at);
  info.append(top, time);
  link.append(boardEl, info);
  return link;
}

function makeSessionFrame(session, entries) {
  const frame = document.createElement('section');
  frame.className = 'timed-history-frame';
  const header = document.createElement('header');
  header.className = 'timed-history-header';
  const details = document.createElement('div');
  details.className = 'timed-history-details';
  const title = document.createElement('strong');
  title.textContent = `${Math.round(session.duration_seconds / 60)} minute session`;
  const settings = document.createElement('span');
  settings.textContent = `${session.mode} · ${session.blindfold_depth} plies`;
  const when = document.createElement('time');
  when.dateTime = session.started_at;
  when.textContent = `${localTime(session.started_at)}${session.ended_at ? ` – ${localTime(session.ended_at)}` : ''}`;
  details.append(title, settings, when);
  const stats = document.createElement('div');
  stats.className = 'timed-history-stats';
  const visibleExpiredCount = Math.max(
    session.expired_count,
    entries.filter(entry => entry.outcome === 'expired').length,
  );
  stats.textContent = `${session.completed_count} completed · ${session.successes} solved · ${session.failures} missed · ${visibleExpiredCount} timed out`;
  header.append(details, stats);
  const grid = document.createElement('div');
  grid.className = 'puzzle-grid';
  for (const entry of entries) grid.appendChild(makePuzzleCard(entry));
  if (!entries.length) {
    const placeholder = document.createElement('p');
    placeholder.className = 'session-no-puzzles';
    placeholder.textContent = 'Session ended before any puzzle was completed.';
    grid.appendChild(placeholder);
  }
  frame.append(header, grid);
  return frame;
}

function renderHistory(entries, sessions) {
  const groupedDays = new Map();
  const ensureDay = (date) => {
    const key = new Date(date).toLocaleDateString();
    if (!groupedDays.has(key)) groupedDays.set(key, { date, entries: [], sessions: new Map() });
    return groupedDays.get(key);
  };

  for (const session of sessions || []) {
    ensureDay(session.started_at).sessions.set(session.id, { session, entries: [] });
  }
  for (const entry of entries) {
    if (entry.session_id) {
      const session = (sessions || []).find(item => item.id === entry.session_id);
      if (session) {
        ensureDay(session.started_at).sessions.get(session.id).entries.push(entry);
        continue;
      }
    }
    ensureDay(entry.completed_at).entries.push(entry);
  }

  daysEl.replaceChildren();
  const days = [...groupedDays.values()].sort((a, b) => new Date(b.date) - new Date(a.date));
  for (const day of days) {
    const section = document.createElement('section');
    section.className = 'history-day';
    const heading = document.createElement('h3');
    const date = document.createElement('span');
    date.textContent = localDay(day.date);
    const dayCount = day.entries.length + [...day.sessions.values()].reduce(
      (total, group) => total + group.entries.length, 0,
    );
    const count = document.createElement('span');
    count.className = 'day-count';
    count.textContent = `${dayCount} ${dayCount === 1 ? 'puzzle' : 'puzzles'}`;
    heading.append(date, count);
    section.appendChild(heading);

    if (day.entries.length) {
      const grid = document.createElement('div');
      grid.className = 'puzzle-grid';
      for (const entry of day.entries) grid.appendChild(makePuzzleCard(entry));
      section.appendChild(grid);
    }
    for (const { session, entries: sessionEntries } of day.sessions.values()) {
      section.appendChild(makeSessionFrame(session, sessionEntries));
    }
    daysEl.appendChild(section);
  }
}

function openSettings() {
  startError.hidden = true;
  startError.textContent = '';
  settingsDialog.showModal();
}

async function startPuzzle(event) {
  event.preventDefault();
  startError.hidden = true;
  startError.textContent = '';
  startButton.disabled = true;
  startButton.textContent = 'Fetching puzzle…';

  const formData = new FormData(settingsForm);
  const payload = {
    mode: formData.get('mode'),
    blindfold_depth: Number(formData.get('blindfold_depth')),
    timed: timedSessionInput.checked,
    duration_minutes: Number(formData.get('duration_minutes')),
  };

  try {
    const response = await fetch('/api/puzzles/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not start a puzzle.');
    window.location.assign(result.redirect_url);
  } catch (error) {
    startError.textContent = error.message || 'Could not start a puzzle. Please try again.';
    startError.hidden = false;
    startButton.disabled = false;
    startButton.textContent = 'Start puzzle';
  }
}

async function loadHistory() {
  try {
    const response = await fetch('/api/puzzles/history');
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    const payload = await response.json();
    displayStats(payload.stats);

    if (!payload.history.length) {
      statusEl.textContent = payload.sessions?.length
        ? 'No completed puzzles yet. Timed sessions will appear here when they finish.'
        : 'No completed puzzles yet. Finish a blindfold puzzle and it will show up here.';
    }
    if (!payload.history.length && !payload.sessions?.length) return;

    renderHistory(payload.history, payload.sessions);
    statusEl.hidden = true;
    contentEl.hidden = false;
  } catch (error) {
    console.error('Could not load puzzle history:', error);
    statusEl.textContent = 'Puzzle history could not be loaded. Please refresh to try again.';
    statusEl.classList.add('error');
  }
}

timedSessionInput.addEventListener('change', () => {
  durationField.hidden = !timedSessionInput.checked;
});
document.getElementById('open-puzzle-settings').addEventListener('click', openSettings);
document.getElementById('close-puzzle-settings').addEventListener('click', () => settingsDialog.close());
document.getElementById('cancel-puzzle-settings').addEventListener('click', () => settingsDialog.close());
settingsDialog.addEventListener('click', (event) => {
  if (event.target === settingsDialog) settingsDialog.close();
});
settingsForm.addEventListener('submit', startPuzzle);

loadHistory();
